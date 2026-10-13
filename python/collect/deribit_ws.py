"""Collecteur tick Deribit -> bronze (JSONL zstd, append-only).

Point d'entree : python -m python.collect.deribit_ws --duration 600

Principes
- Le lecteur WebSocket ne fait QUE : recevoir, horodater, mettre en file.
  Aucun parsing metier, aucune ecriture disque (sinon backpressure -> trous).
- L'ecrivain vide la file et ecrit chaque message tel quel, enveloppe dans
  {"r": recv_ns, "m": <message brut>}. Le bronze reste rejouable a l'identique.
- Les evenements de session (connexion, abonnement, deconnexion) vont dans un
  fichier a part : c'est ce qui permet de prouver ou sont les trous.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

import argparse
import asyncio
import json
import logging
import os
import random
import signal
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import websockets
import zstandard

WS_URL = "wss://www.deribit.com/ws/api/v2"
REST_URL = "https://www.deribit.com/api/v2"
CURRENCIES = ("BTC", "ETH")
SUB_BATCH = 200            # canaux par requete public/subscribe
HEARTBEAT_S = 10
QUEUE_MAX = 200_000
OPEN_TIMEOUT_S = 15        # sans ca, un connect() qui traine bloque le lecteur
CONNECT_CLOSE_TIMEOUT_S = 10
STALE_S = 300              # aucune donnee de souscription pendant ce delai -> reconnexion

log = logging.getLogger("deribit_ws")


# --------------------------------------------------------------------------
# Instruments
# --------------------------------------------------------------------------
def list_option_instruments(currency: str) -> list[str]:
    url = f"{REST_URL}/public/get_instruments?currency={currency}&kind=option&expired=false"
    with urllib.request.urlopen(url, timeout=10) as r:
        return sorted(x["instrument_name"] for x in json.load(r)["result"])


MONTHS = {m: i for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), start=1)}


def parse_option_name(name: str) -> tuple[date, float, bool] | None:
    """BTC-24SEP27-104000-P -> (2027-09-24, 104000.0, False). None si non parsable.

    Mois resolus par table et non par strptime : %b depend de la locale du process,
    et une locale non anglaise ferait echouer la selection ATM en silence.
    """
    parts = name.split("-")
    if len(parts) != 4 or parts[3] not in ("C", "P"):
        return None
    jour_mois = parts[1]
    try:
        jour, mois, annee = int(jour_mois[:-5]), MONTHS[jour_mois[-5:-2]], int(jour_mois[-2:])
        strike = float(parts[2])
        return date(2000 + annee, mois, jour), strike, parts[3] == "C"
    except (KeyError, ValueError):
        return None


def index_price(currency: str) -> float | None:
    """Prix d'indice du perpetuel : sert de repere ATM."""
    url = f"{REST_URL}/public/ticker?instrument_name={currency}-PERPETUAL"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return float(json.load(r)["result"]["index_price"])
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        log.warning("index_price(%s) indisponible: %s", currency, exc)
        return None


def select_ticker_subset(instruments: list[str], spots: dict[str, float],
                         n: int, expiries: int = 5) -> list[str]:
    """n instruments par devise pour le canal ticker.{instrument} (le seul qui porte
    les greeks, l'open interest et bid/ask IV).

    Le canal ticker coute ~1 msg/s et ~770 octets par instrument : le prendre sur
    toute la chaine ferait ~60 Go/jour. On prend donc une bande ATM : les `expiries`
    echeances les plus proches, et autour du spot moitie calls / moitie puts.
    """
    if n <= 0:
        return []
    # trois niveaux : devise -> echeance -> strike -> cote. Chaque niveau doit
    # s'auto-creer, sinon un strike absent leve KeyError (les strikes options ne
    # tombent pas sur des valeurs rondes pour ETH).
    par_devise: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    for name in instruments:
        info = parse_option_name(name)
        if info is None:
            continue
        exp, strike, is_call = info
        par_devise[name.split("-")[0]][exp][strike]["C" if is_call else "P"] = name

    choisis: list[str] = []
    par_echeance = max(2, n // expiries)
    strikes_vises = max(1, par_echeance // 2)
    for devise, echeances in par_devise.items():
        spot = spots.get(devise)
        if not spot:
            continue
        for exp in sorted(echeances)[:expiries]:
            strikes = echeances[exp]
            proches = sorted(strikes, key=lambda s: abs(s - spot))[:strikes_vises]
            for s in proches:
                for side in ("C", "P"):
                    if side in strikes[s]:
                        choisis.append(strikes[s][side])
    return choisis


def channels_for(instruments: list[str], ticker_instruments: list[str] = ()) -> list[str]:
    """Canaux a souscrire.

    quote.* = meilleur bid/ask (98 % du volume, la microstructure).
    Les canaux par devise ajoutent ce qui manquait : mark_iv et mark_price par strike
    pour TOUTE la chaine (markprice.options, ~1 msg/s par devise), DVOL, le perpetuel
    (funding, open interest, base), la reference de settlement, la composition de
    l'indice, et les prints de bloc. Cout total : ~10 msg/s.
    ticker.* uniquement sur la bande ATM : greeks + open interest + bid/ask IV.
    """
    chans = [f"quote.{n}" for n in instruments]
    for cur in CURRENCIES:
        low = cur.lower()
        chans += [
            f"trades.option.{cur}.100ms",
            f"deribit_price_index.{low}_usd",
            f"markprice.options.{low}_usd",           # iv + mark_price, toute la chaine
            f"deribit_volatility_index.{low}_usd",    # DVOL
            f"estimated_expiration_price.{low}_usd",  # reference de settlement
            f"deribit_price_ranking.{low}_usd",       # composition de l'indice
            f"ticker.{cur}-PERPETUAL.100ms",          # funding, OI, base
            f"perpetual.{cur}-PERPETUAL",
            f"trades.{cur}-PERPETUAL.100ms",
            f"block_trades.{cur}",
        ]
    chans += [f"ticker.{n}.100ms" for n in ticker_instruments]
    return chans


# --------------------------------------------------------------------------
# Recuperation des fichiers .part laisses par un arret brutal
# --------------------------------------------------------------------------
def recover_stale_parts(root: Path, min_age_s: float = 60.0, level: int = 6) -> list[dict]:
    """Un arret non propre (reboot, kill -9, coupure) laisse un fichier .part :
    les octets sont sur le disque mais invisibles pour tout ce qui lit
    *.jsonl.zst, donc perdus en silence. On recupere ce qui est lisible (un
    flux zstd tronque perd sa queue, pas sa tete), on recompresse dans un
    fichier propre et on range le .part en .corrupt pour inspection.

    Les fichiers plus recents que min_age_s sont ignores : ils sont peut-etre
    en cours d'ecriture par une session vivante.
    """
    recovered: list[dict] = []
    for part in sorted(root.rglob("*.jsonl.zst.part")):
        try:
            if time.time() - part.stat().st_mtime < min_age_s:
                continue
        except OSError:
            continue

        chunks: list[bytes] = []
        dctx = zstandard.ZstdDecompressor()
        # Decompression INCREMENTALE : un read(n) global leve sur la troncature
        # avant d'avoir rien rendu, et on perdrait tout. En nourrissant les
        # octets compresses par morceaux, on garde ce qui est decodable.
        dobj = dctx.decompressobj()
        try:
            with open(part, "rb") as fh:
                while True:
                    block = fh.read(1 << 20)
                    if not block:
                        break
                    out = dobj.decompress(block)
                    if out:
                        chunks.append(out)
        except (zstandard.ZstdError, OSError):
            pass                      # troncature en fin de flux : le reste est bon

        data = b"".join(chunks)
        lines = [line for line in data.split(b"\n")[:-1] if line.strip()]

        final = part.with_suffix("")            # x.jsonl.zst.part -> x.jsonl.zst
        if lines:
            tmp = final.with_suffix(final.suffix + ".tmp")
            cctx = zstandard.ZstdCompressor(level=level)
            with open(tmp, "wb") as fh, cctx.stream_writer(fh) as zw:
                for line in lines:
                    zw.write(line + b"\n")
            os.replace(tmp, final)

        os.replace(part, part.with_suffix(part.suffix + ".corrupt"))
        recovered.append({"file": final.name if lines else part.name,
                          "recovered_lines": len(lines),
                          "bytes_part": part.stat().st_size if part.exists() else 0})
    return recovered


# --------------------------------------------------------------------------
# Ecrivain bronze : rotation par fenetre de temps, zstd en streaming
# --------------------------------------------------------------------------
class BronzeWriter:
    def __init__(self, root: Path, rotate_s: int, level: int = 6):
        self.root = root
        self.rotate_s = rotate_s
        self.cctx = zstandard.ZstdCompressor(level=level)
        self.window: int | None = None
        self.fh = None
        self.zw = None
        self.files: list[Path] = []

    def _path(self, window: int) -> Path:
        t = time.gmtime(window)
        d = self.root / "provider=deribit" / f"date={time.strftime('%Y-%m-%d', t)}"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{time.strftime('%H%M%SZ', t)}.jsonl.zst"

    def _unique(self, final: Path) -> Path:
        """Deux sessions dans la meme fenetre d'heure (redemarrage, redeploiement)
        visent le meme nom : le rename de la seconde ecraserait le fichier de la
        premiere. Vecu : 8 526 lignes perdues a 10:48 apres un redeploiement.
        On suffixe au lieu d'ecraser ; aucun fichier n'est jamais detruit ici."""
        if not final.exists() and not final.with_suffix(final.suffix + ".part").exists():
            return final
        base = final.name[:-len(".jsonl.zst")]        # 080000Z.jsonl.zst -> 080000Z
        for n in range(1, 1000):
            cand = final.with_name(f"{base}-{n}.jsonl.zst")
            if not cand.exists() and not cand.with_suffix(cand.suffix + ".part").exists():
                return cand
        raise RuntimeError(f"impossible de trouver un nom libre pour {final}")

    def _open(self, window: int) -> None:
        self.close()
        final = self._unique(self._path(window))
        tmp = final.with_suffix(final.suffix + ".part")
        self.fh = open(tmp, "wb")
        self.zw = self.cctx.stream_writer(self.fh)
        self.window = window
        self._tmp, self._final = tmp, final
        log.info("bronze: ouvre %s", final.name)

    def write(self, recv_ns: int, raw: str) -> int:
        window = (recv_ns // 1_000_000_000) // self.rotate_s * self.rotate_s
        if window != self.window:
            self._open(window)
        line = f'{{"r":{recv_ns},"m":{raw}}}\n'.encode()
        assert self.zw is not None
        self.zw.write(line)
        return len(line)

    def close(self) -> None:
        if self.zw is None:
            return
        self.zw.flush(zstandard.FLUSH_FRAME)
        self.zw.close()          # ferme aussi fh
        # rename atomique : un fichier sans .part est complet et lisible
        os.replace(self._tmp, self._final)
        self.files.append(self._final)
        self.zw = self.fh = None


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------
@dataclass
class Stats:
    msgs: int = 0
    raw_bytes: int = 0
    dropped: int = 0
    reconnects: int = 0
    by_kind: dict = field(default_factory=dict)
    last_data_at: float = field(default_factory=time.time)
    stale_events: int = 0


class Collector:
    def __init__(self, out: Path, rotate_s: int, chaos_at: float | None,
                 stale_s: float = STALE_S, ticker_subset: int = 0):
        self.ticker_subset = ticker_subset
        self.ticker_channels = 0
        self.out = out
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.writer = BronzeWriter(out / "bronze", rotate_s)
        self.events_path = out / "events.jsonl"
        self.stats = Stats()
        self.stop = asyncio.Event()
        self.chaos_at = chaos_at
        self.instruments: list[str] = []
        self._ws: Any = None
        self._subscribed = False
        self.stale_s = stale_s

    def event(self, kind: str, **kw) -> None:
        rec = {"t_ns": time.time_ns(), "event": kind, **kw}
        with open(self.events_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        log.info("event %s %s", kind, kw if len(str(kw)) < 200 else "")

    # -- lecteur -----------------------------------------------------------
    async def _session(self) -> None:
        self.instruments = [i for c in CURRENCIES for i in list_option_instruments(c)]
        # La bande ATM est recalculee a chaque session : le spot bouge, et les
        # echeances expirent. Une session dure des heures a des jours.
        spots: dict[str, float] = {}
        for cur in CURRENCIES:
            prix = await asyncio.to_thread(index_price, cur)
            if prix:
                spots[cur] = prix
        ticker_instr = select_ticker_subset(self.instruments, spots, self.ticker_subset)
        self.ticker_channels = len(ticker_instr)
        chans = channels_for(self.instruments, ticker_instr)
        # max_queue : le buffer par defaut (16 trames) deborde pendant la rafale
        # initiale (1 snapshot par canal) -> Deribit coupe avec "connection too slow".
        # open_timeout : sans lui un connect() qui traine bloque le lecteur
        # indefiniment (vecu : 53 h de silence avec un process "vivant").
        try:
            async with websockets.connect(WS_URL, max_size=2**24, max_queue=8192,
                                          ping_interval=None,
                                          open_timeout=OPEN_TIMEOUT_S,
                                          close_timeout=CONNECT_CLOSE_TIMEOUT_S) as ws:
                self._ws = ws
                self.event("connected", n_instruments=len(self.instruments), n_channels=len(chans),
                           n_ticker=self.ticker_channels, spots=spots)
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "public/set_heartbeat",
                                          "params": {"interval": HEARTBEAT_S}}))
                pending = {}
                for i in range(0, len(chans), SUB_BATCH):
                    rid = 1000 + i
                    pending[rid] = len(chans[i:i + SUB_BATCH])
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": "public/subscribe",
                                              "params": {"channels": chans[i:i + SUB_BATCH]}}))
                subscribed = 0
                while not self.stop.is_set():
                    # timeout > 2x heartbeat : si rien n'arrive, la connexion est morte
                    raw = await asyncio.wait_for(ws.recv(), timeout=3 * HEARTBEAT_S)
                    recv_ns = time.time_ns()
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    # filtre texte bon marche : pas de json.loads sur le chemin chaud
                    if '"method":"subscription"' in raw[:60]:
                        # Horodatage de la derniere DONNEE (le heartbeat ne compte pas) :
                        # c'est la seule horloge qui distingue "ca marche" de
                        # "abonne mais muet", cas que le heartbeat masque completement.
                        self.stats.last_data_at = time.time()
                        try:
                            self.queue.put_nowait((recv_ns, raw))
                        except asyncio.QueueFull:
                            self.stats.dropped += 1
                        continue
                    m = json.loads(raw)
                    if m.get("method") == "heartbeat":
                        if m["params"].get("type") == "test_request":
                            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "public/test"}))
                    elif m.get("id") in pending:
                        if "error" in m:
                            self.event("subscribe_error", error=m["error"])
                        else:
                            subscribed += len(m["result"])
                            pending.pop(m["id"])
                            if not pending:
                                self._subscribed = True
                                self.stats.last_data_at = time.time()
                                self.event("subscribed", n_channels=subscribed, requested=len(chans))
        finally:
            # Session terminee : plus rien n'est fiable tant qu'on n'a pas
            # reconnecte ET re-abonne. Le watchdog et instrument_refresh lisent ca.
            self._ws = None
            self._subscribed = False

    async def reader(self) -> None:
        backoff = 1.0
        while not self.stop.is_set():
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # Volontairement large : le lecteur ne doit JAMAIS mourir. Une
                # exception non prevue (API websockets, parse, OS) doit mener a
                # une reconnexion, pas a un collecteur zombi.
                if self.stop.is_set():
                    break
                self.stats.reconnects += 1
                self.event("disconnected", error=type(exc).__name__, detail=str(exc)[:200])
                await asyncio.sleep(backoff + random.random())
                backoff = min(backoff * 2, 60)
            else:
                backoff = 1.0

    # -- ecrivain ----------------------------------------------------------
    async def writer_loop(self) -> None:
        while not (self.stop.is_set() and self.queue.empty()):
            try:
                recv_ns, raw = await asyncio.wait_for(self.queue.get(), timeout=1)
            except asyncio.TimeoutError:
                continue
            self.stats.raw_bytes += self.writer.write(recv_ns, raw)
            self.stats.msgs += 1
            ch = raw[raw.find('"channel":"') + 11:]
            kind = ch[:ch.find(".")]
            self.stats.by_kind[kind] = self.stats.by_kind.get(kind, 0) + 1
        self.writer.close()

    # -- supervision -------------------------------------------------------
    async def reporter(self, every: int = 30) -> None:
        last = 0
        while not self.stop.is_set():
            await asyncio.sleep(every)
            d = self.stats.msgs - last
            last = self.stats.msgs
            log.info("stats: %.0f msg/s, queue=%d, dropped=%d, reconnects=%d, idle=%.0fs, kinds=%s",
                     d / every, self.queue.qsize(), self.stats.dropped,
                     self.stats.reconnects, time.time() - self.stats.last_data_at,
                     self.stats.by_kind)

    async def chaos(self) -> None:
        """Coupe volontairement la connexion pour tester la reprise."""
        await asyncio.sleep(self.chaos_at or 0)
        if self._ws is not None:
            self.event("chaos_close")
            await self._ws.close()

    async def watchdog(self, every_s: float = 15.0) -> None:
        """Filet de securite : le heartbeat de Deribit continue d'arriver meme
        quand les abonnements sont morts, donc la boucle de lecture ne voit rien.
        Si plus AUCUNE donnee n'entre alors qu'on se declare abonne, on ferme la
        connexion -> le lecteur reconnecte et se re-abonne proprement."""
        while not self.stop.is_set():
            await asyncio.sleep(every_s)
            if not self._subscribed or self._ws is None:
                continue          # pas encore abonne : rien a surveiller
            idle = time.time() - self.stats.last_data_at
            if idle > self.stale_s:
                self.stats.stale_events += 1
                self.event("stale_watchdog", idle_s=round(idle, 1),
                           threshold_s=self.stale_s, reconnects=self.stats.reconnects)
                try:
                    await self._ws.close()
                except Exception as exc:  # noqa: BLE001
                    log.warning("stale_watchdog: close a echoue: %s", exc)

    async def instrument_refresh(self, every_s: int = 900) -> None:
        """Echeances qui expirent (08:00 UTC) / nouvelles listees : si la liste
        change, on ferme la session -> reconnexion avec la nouvelle liste.
        Cout : ~2 s de trou, trace dans events.jsonl."""
        while not self.stop.is_set():
            await asyncio.sleep(every_s)
            try:
                fresh = await asyncio.to_thread(
                    lambda: [i for c in CURRENCIES for i in list_option_instruments(c)])
            except OSError as exc:
                log.warning("instrument refresh failed: %s", exc)
                continue
            if set(fresh) != set(self.instruments) and self._subscribed and self._ws is not None:
                added = sorted(set(fresh) - set(self.instruments))
                removed = sorted(set(self.instruments) - set(fresh))
                self.event("instrument_refresh", added=len(added), removed=len(removed),
                           sample_added=added[:5], sample_removed=removed[:5])
                await self._ws.close()

    async def health(self, every_s: int = 15) -> None:
        """Ecrit health.json : lu par le healthcheck Docker et le monitoring."""
        path = self.out / "health.json"
        last_msgs, last_change = -1, time.time()
        while not self.stop.is_set():
            if self.stats.msgs != last_msgs:
                last_msgs, last_change = self.stats.msgs, time.time()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "updated_at": time.time(), "last_msg_at": last_change,
                "last_data_at": self.stats.last_data_at,
                "idle_s": round(time.time() - self.stats.last_data_at, 1),
                "subscribed": self._subscribed,
                "stale_events": self.stats.stale_events,
                "msgs": self.stats.msgs, "dropped": self.stats.dropped,
                "reconnects": self.stats.reconnects, "queue": self.queue.qsize(),
                "n_instruments": len(self.instruments),
                "n_ticker": self.ticker_channels}))
            os.replace(tmp, path)
            await asyncio.sleep(every_s)

    async def run(self, duration: float) -> Stats:
        """duration <= 0 : tourne jusqu'a SIGTERM/SIGINT (mode service)."""
        self.out.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop.set)
        # Avant tout : rapatrier ce qu'un arret brutal a laisse en .part.
        for rec in recover_stale_parts(self.writer.root):
            log.warning("bronze: .part recupere %s (%d lignes)", rec["file"], rec["recovered_lines"])
            self.event("bronze_recovered", **rec)
        self.event("start", duration=duration)
        tasks = [asyncio.create_task(self.reader()),
                 asyncio.create_task(self.writer_loop()),
                 asyncio.create_task(self.reporter()),
                 asyncio.create_task(self.instrument_refresh()),
                 asyncio.create_task(self.watchdog()),
                 asyncio.create_task(self.health())]
        if self.chaos_at:
            tasks.append(asyncio.create_task(self.chaos()))
        if duration > 0:
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=duration)
            except asyncio.TimeoutError:
                pass
        else:
            await self.stop.wait()
        self.stop.set()
        if self._ws is not None:
            await self._ws.close()
        await asyncio.wait(tasks[1:2], timeout=30)   # laisser l'ecrivain vider la file
        for t in tasks:
            t.cancel()
        self.event("stop", msgs=self.stats.msgs, raw_bytes=self.stats.raw_bytes,
                   dropped=self.stats.dropped, reconnects=self.stats.reconnects,
                   files=[str(p) for p in self.writer.files])
        return self.stats


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--duration", type=float, default=600, help="secondes ; 0 = service, tourne jusqu'a SIGTERM")
    p.add_argument("--rotate", type=int, default=3600, help="secondes par fichier bronze")
    p.add_argument("--out", type=Path, default=Path(os.environ.get("DATA_DIR", "data")) / "deribit")
    p.add_argument("--chaos-at", type=float, default=None, help="couper la connexion apres N s")
    p.add_argument("--stale-timeout", type=float, default=STALE_S,
                   help="secondes sans donnee abonnee avant reconnexion forcee")
    p.add_argument("--ticker-subset", type=int, default=20,
                   help="instruments par devise pour le canal ticker (greeks, open interest, "
                        "bid/ask IV). 0 = desactive. ~1 msg/s et ~770 octets par instrument : "
                        "20 par devise coute ~2,8 Go/jour brut, toute la chaine en couterait 60.")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(Collector(a.out, a.rotate, a.chaos_at, a.stale_timeout,
                          a.ticker_subset).run(a.duration))


if __name__ == "__main__":
    main()
