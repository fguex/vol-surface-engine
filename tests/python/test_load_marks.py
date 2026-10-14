"""Tests du chargement bronze -> base.

On teste ce qui peut casser silencieusement : la lecture d'un fichier tronque,
la tranche temporelle, le fait que la derniere valeur d'une tranche gagne, et le
melange des deux sources sur une meme ligne (markprice + ticker).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import zstandard

from python.collect.names import instrument_meta, parse_option_name
from python.jobs.load_marks import (
    COLS_OPTION,
    bucket_of,
    extraire,
    fichiers_a_lire,
    iter_frames,
)


def trame(canal: str, data: dict) -> bytes:
    """Une ligne bronze, dans la forme reelle : {"r": recu, "m": trame}."""
    return json.dumps({"r": 1791871200000000000,
                       "m": {"jsonrpc": "2.0", "method": "subscription",
                             "params": {"channel": canal, "data": data}}}).encode()


def ecrire_zst(chemin: Path, lignes: list[bytes]) -> None:
    chemin.write_bytes(zstandard.ZstdCompressor().compress(b"\n".join(lignes) + b"\n"))


# --------------------------------------------------------------------------
# Lecture du bronze
# --------------------------------------------------------------------------

def _ecrire_comme_le_collecteur(chemin: Path, lignes: list[bytes]) -> None:
    """Reproduit l'ecriture du collecteur : un stream_writer, donc UNE trame zstd
    decoupee en plusieurs blocs. Un .part est cette trame interrompue."""
    cctx = zstandard.ZstdCompressor(level=6)
    with open(chemin, "wb") as fh:
        zw = cctx.stream_writer(fh)
        for ligne in lignes:
            zw.write(ligne + b"\n")
        zw.close()


def test_lecture_fichier_tronque(tmp_path: Path) -> None:
    """Un .part est une trame zstd inachevee : on garde les blocs complets.

    Modele de panne reel : le fichier grossit en continu, on le lit pendant qu'il
    est encore ouvert. Ce qui est complet est lu, la queue est perdue et sera
    relue au passage suivant (le job est idempotent).
    """
    n = 20000
    lignes = [trame("markprice.options.btc_usd",
                    {"instrument_name": f"BTC-24SEP27-{100000 + i}-C", "iv": 40.0 + i % 7,
                     "mark_price": 0.05 + i / 1e5, "timestamp": 1791871200000 + i})
              for i in range(n)]
    entier = tmp_path / "entier.jsonl.zst"
    _ecrire_comme_le_collecteur(entier, lignes)
    brut = entier.read_bytes()

    coupe = tmp_path / "coupe.jsonl.zst"
    coupe.write_bytes(brut[: int(len(brut) * 0.6)])

    lues = list(iter_frames(coupe))
    assert lues, "on doit recuperer les blocs complets d'un fichier tronque"
    assert len(lues) > 0.2 * n, f"trop peu de trames recuperees : {len(lues)}/{n}"
    assert len(lues) < n, "la queue tronquee ne doit pas etre rendue"


def test_lecture_ignore_les_lignes_illisibles(tmp_path: Path) -> None:
    f = tmp_path / "mixte.jsonl.zst"
    ecrire_zst(f, [trame("deribit_price_index.btc_usd", {"price": 1, "timestamp": 1}),
                   b"ceci n est pas du json"])
    assert len(list(iter_frames(f))) == 1


def test_bucket_est_la_tranche_du_marche() -> None:
    # 2026-10-13T06:07:43Z -> 06:05:00Z avec des tranches de 5 min
    ms = int(dt.datetime(2026, 10, 13, 6, 7, 43, tzinfo=dt.timezone.utc).timestamp() * 1000)
    assert bucket_of(ms) == dt.datetime(2026, 10, 13, 6, 5, tzinfo=dt.timezone.utc)
    assert bucket_of(ms, 60) == dt.datetime(2026, 10, 13, 6, 7, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def _fichier(tmp_path: Path, lignes: list[bytes]) -> Path:
    f = tmp_path / "h.jsonl.zst"
    ecrire_zst(f, lignes)
    return f


def test_extrait_marque_et_perpetuel(tmp_path: Path) -> None:
    ts = int(dt.datetime(2026, 10, 13, 6, 7, tzinfo=dt.timezone.utc).timestamp() * 1000)
    f = _fichier(tmp_path, [
        trame("markprice.options.btc_usd",
              {"instrument_name": "BTC-24SEP27-104000-P", "iv": 42.5,
               "mark_price": 0.0512, "timestamp": ts}),
        trame("ticker.BTC-PERPETUAL.100ms",
              {"instrument_name": "BTC-PERPETUAL", "index_price": 83900.0,
               "mark_price": 83910.0, "current_funding": 0.0000125, "funding_8h": 0.0001,
               "open_interest": 1234.5, "timestamp": ts}),
        trame("deribit_volatility_index.btc_usd",
              {"index_name": "btc_usd", "volatility": 51.25, "timestamp": ts}),
    ])
    ech = extraire([f])

    assert len(ech.marks) == 1
    marque = next(iter(ech.marks.values()))
    assert marque["mark_iv"] == 42.5 and marque["mark_price"] == 0.0512
    assert marque["source"] == "markprice"
    assert len(ech.perps) == 1
    assert next(iter(ech.perps.values()))["funding_8h"] == 0.0001
    assert len(ech.dvols) == 1
    assert next(iter(ech.dvols.values()))["underlying"] == "BTC"


def test_les_deux_sources_se_rejoignent(tmp_path: Path) -> None:
    """markprice donne la chaine entiere, ticker les greeks de la bande ATM."""
    ts = int(dt.datetime(2026, 10, 13, 6, 7, tzinfo=dt.timezone.utc).timestamp() * 1000)
    f = _fichier(tmp_path, [
        trame("markprice.options.btc_usd",
              {"instrument_name": "BTC-24SEP27-104000-P", "iv": 42.5,
               "mark_price": 0.0512, "timestamp": ts}),
        trame("ticker.BTC-24SEP27-104000-P.100ms",
              {"instrument_name": "BTC-24SEP27-104000-P", "bid_iv": 41.0, "ask_iv": 44.0,
               "mark_iv": 42.6, "best_bid_price": 0.050, "best_ask_price": 0.052,
               "open_interest": 88.0, "index_price": 83900.0,
               "greeks": {"delta": -0.42, "gamma": 0.0001, "vega": 12.5,
                          "theta": -3.2, "rho": 0.1},
               "timestamp": ts}),
    ])
    ech = extraire([f])
    assert len(ech.marks) == 1, "les deux canaux doivent remplir UNE ligne par tranche"
    marque = next(iter(ech.marks.values()))
    assert marque["source"] == "markprice+ticker"
    assert marque["mark_iv"] == 42.6           # le ticker est arrive apres
    assert marque["delta"] == -0.42 and marque["vega"] == 12.5
    assert marque["open_interest"] == 88.0


def test_derniere_valeur_de_la_tranche_gagne(tmp_path: Path) -> None:
    """Un canal pousse plusieurs fois par seconde : la tranche garde le dernier etat."""
    base = dt.datetime(2026, 10, 13, 6, 5, tzinfo=dt.timezone.utc)
    lignes = []
    for seconde, iv in ((1, 40.0), (60, 41.0), (299, 43.0), (310, 50.0)):
        ts = int((base + dt.timedelta(seconds=seconde)).timestamp() * 1000)
        lignes.append(trame("markprice.options.btc_usd",
                            {"instrument_name": "BTC-24SEP27-104000-P", "iv": iv,
                             "mark_price": 0.05, "timestamp": ts}))
    ech = extraire([_fichier(tmp_path, lignes)])
    assert len(ech.marks) == 2, "299 s et 310 s tombent dans deux tranches differentes"
    premiere = ech.marks[("BTC-24SEP27-104000-P", base)]
    assert premiere["mark_iv"] == 43.0, "dans une tranche, la derniere valeur ecrase"


def test_liste_de_donnees_est_acceptee(tmp_path: Path) -> None:
    """Certains canaux renvoient une liste d'un element, pas un objet."""
    ts = int(dt.datetime(2026, 10, 13, 6, 7, tzinfo=dt.timezone.utc).timestamp() * 1000)
    ligne = json.dumps({"r": 1, "m": {"params": {
        "channel": "markprice.options.btc_usd",
        "data": [{"instrument_name": "BTC-24SEP27-104000-C", "iv": 40.0,
                  "mark_price": 0.05, "timestamp": ts}]}}}).encode()
    ech = extraire([_fichier(tmp_path, [ligne])])
    assert len(ech.marks) == 1


def test_trame_bizarre_n_arrete_pas_le_chargement(tmp_path: Path) -> None:
    """Une trame dont rien n'est exploitable est ignoree, sans ligne vide et sans
    interrompre le reste : le bronze contient des canaux qu'on ne traite pas."""
    ts = 1791871200000
    f = _fichier(tmp_path, [
        trame("markprice.options.btc_usd",
              {"instrument_name": "BTC-24SEP27-104000-P", "iv": None,
               "mark_price": "pas un nombre", "timestamp": ts}),
        trame("ticker.BTC-PERPETUAL.100ms",
              {"instrument_name": "BTC-PERPETUAL", "open_interest": "beaucoup", "timestamp": ts}),
        trame("platform_state", {"state": "ok"}),
        trame("markprice.options.btc_usd",
              {"instrument_name": "BTC-24SEP27-104000-P", "iv": 41.0,
               "mark_price": 0.05, "timestamp": ts}),
    ])
    ech = extraire([f])
    assert len(ech.marks) == 1, "les trames inexploitables ne creent pas de ligne"
    assert len(ech.perps) == 0
    assert next(iter(ech.marks.values()))["mark_iv"] == 41.0, "la bonne trame est bien lue"


def test_colonnes_du_copy_aligne_la_migration() -> None:
    """COLS_OPTION doit lister les colonnes de option_mark : une divergence entre
    le COPY et la table casserait le chargement en production, pas en test."""
    assert "instrument_sk" in COLS_OPTION and "observed_at" in COLS_OPTION
    assert "instrument_name" not in COLS_OPTION, "la table porte l'identifiant, pas le nom"
    assert len(COLS_OPTION) == len(set(COLS_OPTION)) == 19


# --------------------------------------------------------------------------
# Selection des fichiers
# --------------------------------------------------------------------------

def test_selection_par_heure(tmp_path: Path) -> None:
    jour = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    d = tmp_path / "provider=deribit" / f"date={jour}"
    d.mkdir(parents=True)
    for nom in ("000000Z.jsonl.zst", "010000Z.jsonl.zst", "230000Z.jsonl.zst"):
        (d / nom).write_bytes(b"x")
    (d / "010000Z.jsonl.zst.part").write_bytes(b"x")     # en cours d'ecriture

    recents = fichiers_a_lire(tmp_path, since_hours=3, tout=False)
    assert recents, "l'heure courante doit etre selectionnee"
    assert all(f.suffix != ".part" for f in recents), "un .part ne doit jamais etre lu"
    tout = fichiers_a_lire(tmp_path, since_hours=3, tout=True)
    assert len(tout) == 3, "le rattrapage prend tout le bronze"


# --------------------------------------------------------------------------
# Dimension
# --------------------------------------------------------------------------

def test_meta_instrument() -> None:
    opt = instrument_meta("BTC-24SEP27-104000-P")
    assert opt is not None
    assert opt["kind"] == "option" and opt["strike"] == 104000.0
    assert opt["option_type"] == "P" and opt["underlying"] == "BTC"
    assert opt["expiry"] == dt.date(2027, 9, 24)
    perp = instrument_meta("ETH-PERPETUAL")
    assert perp is not None
    assert perp["kind"] == "perpetual" and perp["expiry"] is None and perp["strike"] is None
    assert instrument_meta("btc_usd") is None, "un indice n'est pas un instrument"


def test_parse_partage_avec_le_collecteur() -> None:
    assert parse_option_name("BTC-24SEP27-104000-P") == (dt.date(2027, 9, 24), 104000.0, False)
