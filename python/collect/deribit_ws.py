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

import argparse
import asyncio
import json
import logging
import os
import random
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import websockets
import zstandard

WS_URL = "wss://www.deribit.com/ws/api/v2"
REST_URL = "https://www.deribit.com/api/v2"
CURRENCIES = ("BTC", "ETH")
SUB_BATCH = 200            # canaux par requete public/subscribe
HEARTBEAT_S = 10
QUEUE_MAX = 200_000

log = logging.getLogger("deribit_ws")


# --------------------------------------------------------------------------
# Instruments
# --------------------------------------------------------------------------
def list_option_instruments(currency: str) -> list[str]:
    url = f"{REST_URL}/public/get_instruments?currency={currency}&kind=option&expired=false"
    with urllib.request.urlopen(url, timeout=10) as r:
        return sorted(x["instrument_name"] for x in json.load(r)["result"])


def channels_for(instruments: list[str]) -> list[str]:
    chans = [f"quote.{n}" for n in instruments]
    for cur in CURRENCIES:
        chans.append(f"trades.option.{cur}.100ms")
        chans.append(f"deribit_price_index.{cur.lower()}_usd")
    return chans


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

    def _open(self, window: int) -> None:
        self.close()
        final = self._path(window)
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


class Collector:
    def __init__(self, out: Path, rotate_s: int, chaos_at: float | None):
        self.out = out
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.writer = BronzeWriter(out / "bronze", rotate_s)
        self.events_path = out / "events.jsonl"
        self.stats = Stats()
        self.stop = asyncio.Event()
        self.chaos_at = chaos_at
        self.instruments: list[str] = []
        self._ws = None

    def event(self, kind: str, **kw) -> None:
        rec = {"t_ns": time.time_ns(), "event": kind, **kw}
        with open(self.events_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        log.info("event %s %s", kind, kw if len(str(kw)) < 200 else "")

    # -- lecteur -----------------------------------------------------------
    async def _session(self) -> None:
        self.instruments = [i for c in CURRENCIES for i in list_option_instruments(c)]
        chans = channels_for(self.instruments)
        # max_queue : le buffer par defaut (16 trames) deborde pendant la rafale
        # initiale (1 snapshot par canal) -> Deribit coupe avec "connection too slow".
        async with websockets.connect(WS_URL, max_size=2**24, max_queue=8192,
                                      ping_interval=None) as ws:
            self._ws = ws
            self.event("connected", n_instruments=len(self.instruments), n_channels=len(chans))
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
                            self.event("subscribed", n_channels=subscribed, requested=len(chans))

    async def reader(self) -> None:
        backoff = 1.0
        while not self.stop.is_set():
            try:
                await self._session()
            except (websockets.ConnectionClosed, asyncio.TimeoutError, OSError) as exc:
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
            log.info("stats: %.0f msg/s, queue=%d, dropped=%d, reconnects=%d, kinds=%s",
                     d / every, self.queue.qsize(), self.stats.dropped,
                     self.stats.reconnects, self.stats.by_kind)

    async def chaos(self) -> None:
        """Coupe volontairement la connexion pour tester la reprise."""
        await asyncio.sleep(self.chaos_at or 0)
        if self._ws is not None:
            self.event("chaos_close")
            await self._ws.close()

    async def run(self, duration: float) -> Stats:
        self.out.mkdir(parents=True, exist_ok=True)
        self.event("start", duration=duration)
        tasks = [asyncio.create_task(self.reader()),
                 asyncio.create_task(self.writer_loop()),
                 asyncio.create_task(self.reporter())]
        if self.chaos_at:
            tasks.append(asyncio.create_task(self.chaos()))
        await asyncio.sleep(duration)
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
    p.add_argument("--duration", type=float, default=600)
    p.add_argument("--rotate", type=int, default=3600, help="secondes par fichier bronze")
    p.add_argument("--out", type=Path, default=Path(os.environ.get("DATA_DIR", "data")) / "deribit")
    p.add_argument("--chaos-at", type=float, default=None, help="couper la connexion apres N s")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(Collector(a.out, a.rotate, a.chaos_at).run(a.duration))


if __name__ == "__main__":
    main()
