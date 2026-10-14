"""Test d'integration du chargement bronze -> base, contre une vraie base.

Saute automatiquement si aucune base n'est joignable (poste de dev sans Postgres).
Sur le Mac mini il tourne, et c'est la que deux bugs reels ont ete vus :

- deux chargements successifs dans la MEME transaction creaient la meme table
  temporaire (ON COMMIT DROP ne libere qu'au commit), donc le second echouait ;
- un instrument DEJA EXPIRE charge depuis un bronze ancien naissait avec
  valid_from = maintenant puis etait ferme avec un valid_to dans le passe :
  periode inversee, refusee par le controle de coherence, lot entier perdu.

Il travaille dans une base JETABLE, jamais dans la production : un test qui
ecrit dans la base de production depend de son contenu (un instrument deja
present fait echouer l'insertion) et laisse des traces en cas d'echec.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from pathlib import Path

import psycopg
import pytest

from python.collect.names import instrument_meta
from python.db import connection
from python.db.migrate import main as migrate_main
from python.jobs.load_marks import Echantillon, charger

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _base_joignable() -> bool:
    try:
        with connection.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _base_joignable(), reason="pas de base joignable")


@pytest.fixture
def base_jetable(monkeypatch):
    """Base neuve avec toutes les migrations appliquees, detruite a la fin."""
    nom = f"load_test_{uuid.uuid4().hex[:12]}"
    # La base d'origine est retenue : au demontage, PGDATABASE pointe encore sur la
    # base jetable (monkeypatch ne se defait qu'apres), et on ne peut pas supprimer
    # la base sur laquelle on est connecte.
    origine = os.environ.get("PGDATABASE", "postgres")
    with connection.connect() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{nom}"')
    monkeypatch.setenv("PGDATABASE", nom)
    assert migrate_main(["--sql-dir", str(SQL_DIR)]) == 0
    yield nom
    with psycopg.connect(dbname=origine) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')


def _echantillon(ts: dt.datetime) -> Echantillon:
    """Un option, un perpetuel et un DVOL : les trois familles d'un coup."""
    ech = Echantillon()
    nom_opt = "BTC-24SEP27-104000-P"
    meta_opt = instrument_meta(nom_opt)
    assert meta_opt is not None
    ech.voit(nom_opt, meta_opt, ts)
    ech.marks[(nom_opt, ts)] = {
        "instrument_name": nom_opt, "observed_at": ts, "source": "markprice",
        "mark_iv": 42.5, "mark_price": 0.05}

    meta_perp = instrument_meta("BTC-PERPETUAL")
    assert meta_perp is not None
    ech.voit("BTC-PERPETUAL", meta_perp, ts)
    ech.perps[("BTC-PERPETUAL", ts)] = {
        "instrument_name": "BTC-PERPETUAL", "observed_at": ts, "index_price": 83900.0,
        "funding_8h": 0.0001}
    ech.dvols[("BTC", ts)] = {"underlying": "BTC", "observed_at": ts, "volatility": 51.0}
    return ech


def test_les_trois_familles_s_ecrivent(base_jetable) -> None:
    """option_mark, perp_tick et vol_index a la suite : chacun cree sa table
    temporaire. Le bug historique etait un nom partage entre les trois."""
    ts = dt.datetime(2026, 10, 13, 6, 5, tzinfo=dt.timezone.utc)
    ech = _echantillon(ts)
    with connection.connect() as conn:
        stats = charger(conn, ech)
        conn.commit()
        assert stats["option_mark"] == 1, stats
        assert stats["perp_tick"] == 1, stats
        assert stats["vol_index"] == 1, stats

    # rejouer le meme echantillon ne doit RIEN ajouter : c'est la garantie qui
    # rend un rattrapage ou une restauration sans danger
    with connection.connect() as conn:
        encore = charger(conn, ech)
        conn.commit()
        assert encore["option_mark"] == 0, encore
        assert encore["perp_tick"] == 0, encore
        assert encore["vol_index"] == 0, encore


def test_instrument_deja_expire_garde_une_periode_coherente(base_jetable) -> None:
    """Un instrument expire charge depuis un bronze ancien doit naitre avec la
    date de sa PREMIERE observation, pas celle du chargement.

    Sinon sa periode est inversee et le controle de coherence refuse la ligne.
    """
    ts = dt.datetime(2026, 10, 13, 6, 5, tzinfo=dt.timezone.utc)   # expire le 13 a 08:00
    nom = "ETH-13OCT26-2540-P"
    ech = Echantillon()
    meta = instrument_meta(nom)
    assert meta is not None
    ech.voit(nom, meta, ts)
    ech.marks[(nom, ts)] = {"instrument_name": nom, "observed_at": ts,
                            "source": "markprice", "mark_iv": 50.0, "mark_price": 0.01}

    with connection.connect() as conn:
        stats = charger(conn, ech, aujourdhui=dt.date(2026, 10, 14))
        conn.commit()
        assert stats["option_mark"] == 1, stats
        assert stats["fermes"] == 1, stats
        with conn.cursor() as cur:
            cur.execute("""SELECT valid_from, valid_to FROM silver.instrument
                           WHERE instrument_name = %s AND valid_to IS NOT NULL""", (nom,))
            ligne = cur.fetchone()
        assert ligne is not None, "l'instrument doit exister : la cle etrangere en depend"
        debut, fin = ligne
        assert debut < fin, (debut, fin)
        assert fin == dt.datetime(2026, 10, 13, 8, 0, tzinfo=dt.timezone.utc), (
            "la fin de validite est l'echeance Deribit : 08:00 UTC")


def test_double_chargement_dans_une_meme_transaction(base_jetable) -> None:
    """Deux appels dans la meme transaction, sans commit intermediaire : c'est le
    cas d'un rejeu de rattrapage dans un seul lot."""
    ts = dt.datetime(2026, 10, 13, 6, 10, tzinfo=dt.timezone.utc)
    ech = _echantillon(ts)
    with connection.connect() as conn:
        with conn.transaction():
            premier = charger(conn, ech)
            second = charger(conn, ech)
        assert premier["option_mark"] == 1, premier
        assert second["option_mark"] == 0, "le rejeu ne doit pas creer de doublon"
        conn.rollback()


def test_reobservation_apres_fermeture_ne_cree_pas_de_version(base_jetable) -> None:
    """Le cas qui a casse le premier rattrapage complet.

    Une option observee a 07:50 est fermee a 08:00 (son echeance). Le lot suivant
    la revoit a 07:55 : on doit reutiliser la version existante, pas en creer une
    seconde qui la chevauche (la contrainte d'exclusion refuserait tout le lot).
    """
    nom = "ETH-13OCT26-2520-P"
    meta = instrument_meta(nom)
    assert meta is not None

    def lot(minute: int) -> Echantillon:
        ts = dt.datetime(2026, 10, 13, 7, minute, tzinfo=dt.timezone.utc)
        ech = Echantillon()
        ech.voit(nom, meta, ts)
        ech.marks[(nom, ts)] = {"instrument_name": nom, "observed_at": ts,
                                "source": "markprice", "mark_iv": 48.0, "mark_price": 0.02}
        return ech

    with connection.connect() as conn:
        premier = charger(conn, lot(50), aujourdhui=dt.date(2026, 10, 14))
        conn.commit()
        assert premier["option_mark"] == 1, premier
        assert premier["fermes"] == 1, "l'echeance du 13 a 08:00 a ferme la version"

        # meme instrument, cinq minutes plus tard, dans un autre lot
        second = charger(conn, lot(55), aujourdhui=dt.date(2026, 10, 14))
        conn.commit()
        assert second["option_mark"] == 1, second
        assert second["fermes"] == 0, "deja ferme : rien a fermer de plus"

        with conn.cursor() as cur:
            cur.execute("SELECT count(*), max(valid_to) FROM silver.instrument WHERE instrument_name = %s",
                        (nom,))
            nombre, fin = cur.fetchone()
        assert nombre == 1, f"une seule version attendue, {nombre} trouvee(s)"
        assert fin is not None, "la version doit rester fermee"
