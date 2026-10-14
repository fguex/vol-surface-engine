"""Bronze Deribit -> base : etats echantillonnes de la chaine et du perpetuel.

Pourquoi ce job existe
- Le bronze est brut : un message par changement de carnet. Le mettre tel quel en
  base serait absurde (des centaines de millions de lignes par jour) et
  interromprait l'avantage du fichier : rejouable, compressible, deplacable.
- La base ne doit contenir que ce qu'on interroge : l'etat de la chaine
  (mark_iv, mark_price par strike), le perpetuel (funding, base, open interest)
  et DVOL, echantillonnes a cadence fixe.

Deux proprietes qui comptent, verifiees par les tests :
- IDEMPOTENT : rejouer une plage de bronze ne cree jamais de doublon. La cle est
  (instrument_sk, observed_at) et l'insertion se fait en ON CONFLICT DO NOTHING.
  C'est ce qui rend un rattrapage, ou une restauration apres sinistre, sans
  danger : on rejoue, on compare, on ne duplique pas.
- DETERMINISTE : observed_at vient de l'horodatage du fournisseur, jamais de
  l'heure d'execution. Rejouer le meme bronze donne les memes lignes, sinon la
  reproductibilite des fits (docs/data_architecture_medallion.md) tombe.

Usage
    python -m python.jobs.load_marks                    # dernieres heures
    python -m python.jobs.load_marks --since-hours 6
    python -m python.jobs.load_marks --all              # tout le bronze
    python -m python.jobs.load_marks --dry-run          # extraire sans ecrire
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import zstandard

from python.collect.names import instrument_meta
from python.db import connection

log = logging.getLogger("load_marks")

BUCKET_S = 300           # 5 minutes : la cadence des etats en base
CHUNK = 1 << 20
NOM_FICHIER = re.compile(r"^(\d{2})(\d{2})(\d{2})Z")

# Colonnes, dans l'ordre du COPY. Doit rester aligne sur sql/003_deribit.sql.
COLS_OPTION = ("observed_at", "instrument_sk", "mark_iv", "mark_price", "underlying_price",
               "index_price", "bid_iv", "ask_iv", "bid_price", "ask_price", "bid_amount",
               "ask_amount", "open_interest", "delta", "gamma", "vega", "theta", "rho",
               "source")
COLS_PERP = ("observed_at", "instrument_sk", "index_price", "mark_price", "last_price",
             "best_bid_price", "best_ask_price", "current_funding", "funding_8h",
             "interest_rate", "open_interest")
COLS_VOL = ("observed_at", "underlying", "volatility")


# --------------------------------------------------------------------------
# Lecture du bronze
# --------------------------------------------------------------------------

def read_all(path: Path) -> bytes:
    """Decode un fichier zstd, meme tronque.

    Un .part est en cours d'ecriture : zstandard leve une erreur sur une trame
    incomplete. On garde ce qui est lisible plutot que de tout jeter. On ne
    compresse jamais a la fin d'une trame coupee : ce qui manque sera relu au
    passage suivant (le job est idempotent, rien n'est perdu).
    """
    dctx = zstandard.ZstdDecompressor()
    out = bytearray()
    with open(path, "rb") as fh, dctx.stream_reader(fh) as reader:
        while True:
            try:
                bloc = reader.read(CHUNK)
            except zstandard.ZstdError:
                break
            if not bloc:
                break
            out += bloc
    return bytes(out)


def iter_frames(path: Path):
    """Rend les messages Deribit d'un fichier bronze (l'element 'm' de chaque ligne)."""
    for ligne in read_all(path).split(b"\n"):
        if not ligne.strip():
            continue
        try:
            enveloppe = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        msg = enveloppe.get("m", enveloppe)
        if isinstance(msg, dict):
            yield msg


def bucket_of(ts_ms: float, bucket_s: int = BUCKET_S) -> dt.datetime:
    """Instant du marche ramene au debut de sa tranche, en UTC."""
    return dt.datetime.fromtimestamp((int(ts_ms) // 1000) // bucket_s * bucket_s,
                                     tz=dt.timezone.utc)


def _first(data):
    """Les canaux Deribit renvoient un objet ou une liste d'un objet."""
    if isinstance(data, list):
        return data[0] if data else None
    return data


def _f(valeur):
    """Convertit en float, ou None. Aucune exception ne doit sortir d'ici : une
    trame bizarre ne doit pas arreter un chargement de plusieurs heures."""
    if valeur is None or isinstance(valeur, bool):
        return None
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return None


@dataclass
class Echantillon:
    """Ce qu'on extrait du bronze : des dimensions et trois familles de faits."""
    instruments: dict = field(default_factory=dict)          # nom -> meta
    marks: dict = field(default_factory=dict)                # (nom, tranche) -> row
    perps: dict = field(default_factory=dict)
    dvols: dict = field(default_factory=dict)
    trames: int = 0
    ignorees: int = 0

    def fusionne_marque(self, nom: str, cle, valeurs: dict, source: str) -> None:
        """Ajoute (ou complete) la marque d'un instrument dans une tranche.

        Une trame dont aucune valeur n'est exploitable ne cree pas de ligne :
        une ligne entierement NULL en base n'informe de rien et coute un aller
        en ecriture. Si la ligne existe deja (l'autre source a parle), on la
        complete simplement.
        """
        utiles = {k: v for k, v in valeurs.items() if v is not None}
        row = self.marks.get((nom, cle))
        if row is None:
            if not utiles:
                return
            row = {"instrument_name": nom, "observed_at": cle, "source": source}
            self.marks[(nom, cle)] = row
        elif row["source"] != source:
            row["source"] = "markprice+ticker"
        row.update(utiles)


def extraire(fichiers, bucket_s: int = BUCKET_S) -> Echantillon:
    ech = Echantillon()
    for chemin in fichiers:
        for msg in iter_frames(chemin):
            params = msg.get("params") or {}
            canal = str(params.get("channel") or "")
            data = _first(params.get("data"))
            if not isinstance(data, dict):
                continue
            ech.trames += 1
            famille = canal.split(".")[0]
            nom = data.get("instrument_name") or ""
            ts = _f(data.get("timestamp"))

            if famille == "markprice" and ts is not None:
                # mark_iv + mark_price sur TOUTE la chaine : la matiere premiere
                # de la surface, y compris les strikes que le carnet ne suit pas.
                meta = instrument_meta(nom)
                if meta:
                    ech.instruments[nom] = meta
                    ech.fusionne_marque(nom, bucket_of(ts, bucket_s),
                                        {"mark_iv": _f(data.get("iv")),
                                         "mark_price": _f(data.get("mark_price"))},
                                        "markprice")
            elif famille == "ticker" and ts is not None and nom:
                greeks = data.get("greeks") or {}
                if nom.endswith("PERPETUAL"):
                    meta = instrument_meta(nom)
                    if meta:
                        ech.instruments[nom] = meta
                        cle = bucket_of(ts, bucket_s)
                        row = {
                            "instrument_name": nom, "observed_at": cle,
                            "index_price": _f(data.get("index_price")),
                            "mark_price": _f(data.get("mark_price")),
                            "last_price": _f(data.get("last_price")),
                            "best_bid_price": _f(data.get("best_bid_price")),
                            "best_ask_price": _f(data.get("best_ask_price")),
                            "current_funding": _f(data.get("current_funding")),
                            "funding_8h": _f(data.get("funding_8h")),
                            "interest_rate": _f(data.get("interest_rate")),
                            "open_interest": _f(data.get("open_interest")),
                        }
                        if any(v is not None for k, v in row.items()
                               if k not in ("instrument_name", "observed_at")):
                            ech.perps[(nom, cle)] = row
                else:
                    meta = instrument_meta(nom)
                    if meta:
                        ech.instruments[nom] = meta
                        ech.fusionne_marque(nom, bucket_of(ts, bucket_s), {
                            "mark_iv": _f(data.get("mark_iv")),
                            "mark_price": _f(data.get("mark_price")),
                            "underlying_price": _f(data.get("underlying_price")),
                            "index_price": _f(data.get("index_price")),
                            "bid_iv": _f(data.get("bid_iv")),
                            "ask_iv": _f(data.get("ask_iv")),
                            "bid_price": _f(data.get("best_bid_price")),
                            "ask_price": _f(data.get("best_ask_price")),
                            "bid_amount": _f(data.get("best_bid_amount")),
                            "ask_amount": _f(data.get("best_ask_amount")),
                            "open_interest": _f(data.get("open_interest")),
                            "delta": _f(greeks.get("delta")),
                            "gamma": _f(greeks.get("gamma")),
                            "vega": _f(greeks.get("vega")),
                            "theta": _f(greeks.get("theta")),
                            "rho": _f(greeks.get("rho")),
                        }, "ticker")
            elif famille == "deribit_volatility_index" and ts is not None:
                # index_name vaut btc_usd / eth_usd.
                devise = str(data.get("index_name") or "").split("_")[0].upper()
                vol = _f(data.get("volatility"))
                if devise and vol is not None:
                    ech.dvols[(devise, bucket_of(ts, bucket_s))] = {
                        "underlying": devise, "observed_at": bucket_of(ts, bucket_s),
                        "volatility": vol}
            else:
                ech.ignorees += 1
    return ech


# --------------------------------------------------------------------------
# Ecriture
# --------------------------------------------------------------------------

def ancrer_instruments(conn, echantillon: Echantillon) -> dict:
    """Cree les instruments manquants, et rend {nom: instrument_sk}.

    SCD2 : on n'ecrit jamais par-dessus une version. Si un instrument disparait
    de la liste (il a expire), sa version courante est fermee, pas supprimee.
    """
    if not echantillon.instruments:
        return {}
    with conn.cursor() as cur:
        for nom, meta in echantillon.instruments.items():
            cur.execute(
                """INSERT INTO instrument (provider, underlying, instrument_name, kind,
                                           expiry, strike, option_type, is_inverse,
                                           contract_size, quote_currency)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (provider, instrument_name) WHERE valid_to IS NULL
                   DO NOTHING""",
                (meta["provider"], meta["underlying"], nom, meta["kind"], meta["expiry"],
                 meta["strike"], meta["option_type"], meta["is_inverse"],
                 meta["contract_size"], meta["quote_currency"]))
        cur.execute(
            """SELECT instrument_name, instrument_sk FROM instrument
               WHERE valid_to IS NULL AND instrument_name = ANY(%s)""",
            (list(echantillon.instruments),))
        return dict(cur.fetchall())


def fermer_expires(conn, aujourdhui: dt.date) -> int:
    """Ferme la version courante des options arrivees a echeance.

    Une option Deribit expire a 08:00 UTC le jour de son echeance : c'est la fin
    reelle de validite de l'instrument, donc la fin de sa version.
    """
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE instrument
                  SET valid_to = (expiry + interval '8 hours')::timestamptz
                WHERE valid_to IS NULL AND kind = 'option' AND expiry < %s""",
            (aujourdhui,))
        return cur.rowcount


def inserer(conn, table: str, cols: tuple, rows: list) -> int:
    """COPY dans une table temporaire puis INSERT ... ON CONFLICT DO NOTHING.

    COPY est le seul moyen d'ecrire des dizaines de milliers de lignes en une
    fraction de seconde ; le ON CONFLICT final garantit qu'un rejeu ne duplique
    rien. Une insertion ligne a ligne prendrait des minutes et n'apporterait rien.
    """
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.execute(f"CREATE TEMP TABLE tmp_load (LIKE {table}) ON COMMIT DROP")
        colonnes = ", ".join(cols)
        with cur.copy(f"COPY tmp_load ({colonnes}) FROM STDIN") as cp:
            for row in rows:
                cp.write_row(tuple(row[c] for c in cols))
        cur.execute(f"INSERT INTO {table} ({colonnes}) SELECT {colonnes} FROM tmp_load "
                    f"ON CONFLICT DO NOTHING")
        return cur.rowcount


def charger(conn, echantillon: Echantillon, aujourdhui: dt.date | None = None) -> dict:
    sk = ancrer_instruments(conn, echantillon)
    inconnus = {n for n in list(echantillon.instruments) if n not in sk}

    def lignes(source: dict, cols: tuple) -> list:
        out = []
        for row in source.values():
            ident = sk.get(row["instrument_name"])
            if ident is None:
                continue
            complet = dict(row, instrument_sk=ident)
            out.append({c: complet.get(c) for c in cols})
        return out

    stats = {
        "option_mark": inserer(conn, "option_mark", COLS_OPTION,
                               lignes(echantillon.marks, COLS_OPTION)),
        "perp_tick": inserer(conn, "perp_tick", COLS_PERP,
                             lignes(echantillon.perps, COLS_PERP)),
        "vol_index": inserer(conn, "vol_index", COLS_VOL,
                             [{"observed_at": r["observed_at"], "underlying": r["underlying"],
                               "volatility": r["volatility"]} for r in echantillon.dvols.values()]),
        "instruments": len(sk),
        "instruments_inconnus": sorted(inconnus)[:5],
        "fermes": fermer_expires(conn, aujourdhui or dt.date.today()),
    }
    return stats


# --------------------------------------------------------------------------
# Selection des fichiers
# --------------------------------------------------------------------------

def fichiers_a_lire(bronze: Path, since_hours: float, tout: bool) -> list[Path]:
    """Fichiers bronze a traiter.

    Le nom porte l'heure de debut (`060000Z.jsonl.zst` dans `date=2026-10-13/`) :
    on peut donc selectionner sans ouvrir les fichiers. Les `.part` sont exclus
    par defaut : ils changent sous nos pieds. Le fichier de l'heure en cours
    sera lu au passage suivant, quand il sera ferme.
    """
    limite = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=since_hours)
    out = []
    for chemin in sorted(bronze.glob("provider=deribit/date=*/*.jsonl.zst")):
        if tout:
            out.append(chemin)
            continue
        m = NOM_FICHIER.match(chemin.name)
        if not m:
            continue
        h, mi, s = (int(x) for x in m.groups())
        jour = chemin.parent.name.split("=")[1]
        try:
            debut = dt.datetime.fromisoformat(f"{jour}T{h:02d}:{mi:02d}:{s:02d}+00:00")
        except ValueError:
            continue
        if debut >= limite:
            out.append(chemin)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bronze", type=Path,
                   default=Path(os.environ.get("DATA_DIR", "data")) / "deribit" / "bronze")
    p.add_argument("--since-hours", type=float, default=3.0,
                   help="traiter les fichiers des N dernieres heures (defaut 3)")
    p.add_argument("--all", action="store_true", help="tout le bronze : rattrapage")
    p.add_argument("--bucket-seconds", type=int, default=BUCKET_S,
                   help="largeur d'une tranche temporelle (defaut 300 s)")
    p.add_argument("--dry-run", action="store_true", help="extraire sans ecrire en base")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    fichiers = fichiers_a_lire(a.bronze, a.since_hours, a.all)
    if not fichiers:
        log.info("aucun fichier bronze a traiter (bronze=%s)", a.bronze)
        return 0
    log.info("%d fichier(s) a traiter, tranches de %d s", len(fichiers), a.bucket_seconds)

    ech = extraire(fichiers, a.bucket_seconds)
    log.info("extrait : %d trames, %d marques, %d ticks perpetuel, %d DVOL, %d instruments",
             ech.trames, len(ech.marks), len(ech.perps), len(ech.dvols),
             len(ech.instruments))
    if a.dry_run:
        for cle, row in list(ech.marks.items())[:3]:
            log.info("exemple %s -> %s", cle, {k: v for k, v in row.items() if v is not None})
        return 0

    with connection.connect() as conn:
        with conn.transaction():
            stats = charger(conn, ech)
    log.info("ecrit : %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
