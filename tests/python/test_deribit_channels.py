"""Tests de la selection des canaux Deribit (parsing, bande ATM, liste finale)."""

from __future__ import annotations

from datetime import date

from python.collect.deribit_ws import (
    channels_for,
    parse_option_name,
    select_ticker_subset,
)


def test_parse_nom_option() -> None:
    assert parse_option_name("BTC-24SEP27-104000-P") == (date(2027, 9, 24), 104000.0, False)
    assert parse_option_name("BTC-24SEP27-104000-C") == (date(2027, 9, 24), 104000.0, True)
    assert parse_option_name("ETH-31DEC26-2800-C") == (date(2026, 12, 31), 2800.0, True)


def test_parse_jour_a_un_chiffre() -> None:
    """Deribit n'ecrit pas le jour sur deux chiffres partout (ex. 3OCT26)."""
    assert parse_option_name("BTC-3OCT26-100000-C") == (date(2026, 10, 3), 100000.0, True)


def test_parse_rejette_le_non_parsable() -> None:
    for mauvais in ["BTC-PERPETUAL", "BTC-24SEP27-104000", "BTC-24XXX27-1000-C",
                    "BTC-24SEP27-abc-C", "", "BTC-24SEP27-1000-X"]:
        assert parse_option_name(mauvais) is None, mauvais


def _chaine(devise: str, spot: float) -> list[str]:
    """Chaine synthetique : 4 echeances, strikes de 60 % a 140 % du spot."""
    noms = []
    for exp in ["24SEP26", "31DEC26", "26MAR27", "25JUN27"]:
        for mult in (0.6, 0.8, 0.95, 1.0, 1.05, 1.2, 1.4):
            strike = int(spot * mult)
            noms += [f"{devise}-{exp}-{strike}-C", f"{devise}-{exp}-{strike}-P"]
    return noms


def test_subset_nul_renvoie_vide() -> None:
    assert select_ticker_subset(_chaine("BTC", 80000), {"BTC": 80000.0}, 0) == []


def test_subset_prend_les_strikes_proches_du_spot() -> None:
    noms = _chaine("BTC", 80000)
    choisis = select_ticker_subset(noms, {"BTC": 80000.0}, 20)
    assert choisis, "la selection ne doit pas etre vide"
    strikes = sorted({float(n.split("-")[2]) for n in choisis})
    # 80000 et 84000 (1.05) sont les plus proches ; 48000 et 112000 ne doivent pas y etre
    assert 48000 not in strikes and 112000 not in strikes
    assert any(abs(s - 80000) <= 8000 for s in strikes)


def test_subset_prend_les_deux_cotes_et_plusieurs_echeances() -> None:
    choisis = select_ticker_subset(_chaine("BTC", 80000), {"BTC": 80000.0}, 20)
    assert any(n.endswith("-C") for n in choisis)
    assert any(n.endswith("-P") for n in choisis)
    echeances = {n.split("-")[1] for n in choisis}
    assert len(echeances) >= 3, f"les echeances proches doivent toutes etre couvertes : {echeances}"


def test_subset_est_par_devise() -> None:
    noms = _chaine("BTC", 80000) + _chaine("ETH", 3000)
    spots = {"BTC": 80000.0, "ETH": 3000.0}
    choisis = select_ticker_subset(noms, spots, 20)
    btc = [n for n in choisis if n.startswith("BTC")]
    eth = [n for n in choisis if n.startswith("ETH")]
    assert len(btc) >= 8 and len(eth) >= 8, (len(btc), len(eth))


def test_subset_ignore_une_devise_sans_spot() -> None:
    noms = _chaine("BTC", 80000) + _chaine("ETH", 3000)
    choisis = select_ticker_subset(noms, {"BTC": 80000.0}, 20)
    assert choisis and all(n.startswith("BTC") for n in choisis)


def test_canaux_incluent_ce_qui_manquait() -> None:
    chans = channels_for(["BTC-24SEP27-100000-C"])
    for attendu in [
        "quote.BTC-24SEP27-100000-C",
        "markprice.options.btc_usd",
        "markprice.options.eth_usd",
        "deribit_volatility_index.btc_usd",
        "estimated_expiration_price.btc_usd",
        "deribit_price_ranking.btc_usd",
        "ticker.BTC-PERPETUAL.100ms",
        "perpetual.BTC-PERPETUAL",
        "trades.BTC-PERPETUAL.100ms",
        "block_trades.BTC",
        "block_trades.ETH",
        "ticker.ETH-PERPETUAL.100ms",
    ]:
        assert attendu in chans, attendu


def test_canaux_ticker_seulement_sur_le_sous_ensemble() -> None:
    instruments = ["BTC-24SEP27-100000-C", "BTC-24SEP27-110000-C"]
    chans = channels_for(instruments, ticker_instruments=["BTC-24SEP27-100000-C"])
    assert "ticker.BTC-24SEP27-100000-C.100ms" in chans
    assert "ticker.BTC-24SEP27-110000-C.100ms" not in chans
    # le book, lui, reste sur tout l'univers
    assert "quote.BTC-24SEP27-110000-C" in chans


def test_canaux_sans_subset() -> None:
    chans = channels_for([], [])
    assert not any(c.startswith("ticker.BTC-") and "PERPETUAL" not in c for c in chans)
    assert len(chans) == 20  # 10 canaux par devise, tous "always on"
