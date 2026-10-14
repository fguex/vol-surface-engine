"""Test d'integration du chargement bronze -> base, contre une vraie base.

Saute automatiquement si aucune base n'est joignable (poste de dev sans Postgres).
Sur le Mac mini il tourne, et c'est la que le bug a ete vu : deux chargements
successifs dans la MEME transaction creaient deux fois la meme table temporaire
(ON COMMIT DROP ne libere qu'au commit), donc le deuxieme echouait.

Il ne touche jamais la production : tout se passe dans une transaction annulee.
"""

from __future__ import annotations

import datetime as dt

import pytest

from python.db import connection
from python.jobs.load_marks import Echantillon, charger


def _base_joignable() -> bool:
    try:
        with connection.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _base_joignable(), reason="pas de base joignable")


def _echantillon(ts: dt.datetime) -> Echantillon:
    ech = Echantillon()
    ech.instruments["BTC-24SEP27-104000-P"] = {
        "provider": "deribit", "underlying": "BTC", "instrument_name": "BTC-24SEP27-104000-P",
        "kind": "option", "expiry": dt.date(2027, 9, 24), "strike": 104000.0,
        "option_type": "P", "is_inverse": True, "contract_size": 1.0, "quote_currency": "BTC"}
    ech.instruments["BTC-PERPETUAL"] = {
        "provider": "deribit", "underlying": "BTC", "instrument_name": "BTC-PERPETUAL",
        "kind": "perpetual", "expiry": None, "strike": None, "option_type": None,
        "is_inverse": True, "contract_size": 1.0, "quote_currency": "BTC"}
    ech.marks[("BTC-24SEP27-104000-P", ts)] = {
        "instrument_name": "BTC-24SEP27-104000-P", "observed_at": ts, "source": "markprice",
        "mark_iv": 42.5, "mark_price": 0.05}
    ech.perps[("BTC-PERPETUAL", ts)] = {
        "instrument_name": "BTC-PERPETUAL", "observed_at": ts, "index_price": 83900.0,
        "funding_8h": 0.0001}
    ech.dvols[("BTC", ts)] = {"underlying": "BTC", "observed_at": ts, "volatility": 51.0}
    return ech


def test_trois_chargements_dans_une_transaction() -> None:
    """option_mark, perp_tick et vol_index s'ecrivent a la suite : chacun cree sa
    table temporaire. Le bug historique etait un nom partage entre les trois."""
    # une tranche ancienne, pour ne pas polluer les donnees recentes de la base
    ts = dt.datetime(2020, 1, 1, 0, 5, tzinfo=dt.timezone.utc)
    ech = _echantillon(ts)

    with connection.connect() as conn:
        with conn.transaction():
            stats = charger(conn, ech)
            assert stats["option_mark"] == 1, stats
            assert stats["perp_tick"] == 1, stats
            assert stats["vol_index"] == 1, stats

            # Rejouer le meme echantillon ne doit RIEN ajouter : c'est la garantie
            # qui rend un rattrapage ou une restauration sans danger.
            encore = charger(conn, ech)
            assert encore["option_mark"] == 0, encore
            assert encore["perp_tick"] == 0, encore
            assert encore["vol_index"] == 0, encore
        conn.rollback()
