"""Contrat de schema avec le flux CBOE.

SEUL endroit du projet ou apparaissent les noms de colonnes du provider.
Le jour ou tu changes de provider (ou de version d'OpenBB), tu modifies
ce fichier et rien d'autre.

Noms verifies le 2026-09-11 avec openbb-cboe sur SPX/SPY/AAPL.
"""

from __future__ import annotations

import logging

import polars as pl

log = logging.getLogger(__name__)

UTC = pl.Datetime(time_zone="UTC")

# ---------------------------------------------------------------------------
# Noms REELS renvoyes par openbb-cboe.
# Invariant C7 : on ingere large. Tout ce que le provider donne est conserve
# dans le Parquet brut, meme ce que le C++ ne consomme pas.
# ---------------------------------------------------------------------------
RAW_SCHEMA: dict[str, pl.DataType] = {
    # --- identite du contrat ---
    "contract_symbol": pl.Utf8,       # ex. SPXW260911C02800000 (format OCC)
    "underlying_symbol": pl.Utf8,
    "expiration": pl.Date,
    "strike": pl.Float64,
    "option_type": pl.Utf8,           # 'call' | 'put'
    "dte": pl.Int32,                  # jours jusqu'a expiration, selon CBOE
    # --- marche ---
    "bid": pl.Float64,
    "ask": pl.Float64,
    "bid_size": pl.Int32,
    "ask_size": pl.Int32,
    "volume": pl.Int64,
    "open_interest": pl.Int64,
    "last_trade_price": pl.Float64,
    "last_trade_time": UTC,           # ATTENTION : derniere TRANSACTION, pas
                                      # derniere cotation. Massivement nul.
                                      # NE PAS utiliser comme mesure de fraicheur.
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "prev_close": pl.Float64,
    "change": pl.Float64,
    "change_percent": pl.Float64,
    "tick": pl.Utf8,
    # --- sous-jacent ---
    "underlying_price": pl.Float64,
    # --- valeurs calculees par CBOE ---------------------------------------
    # Conventions de temps / taux / dividendes INCONNUES et probablement
    # differentes des notres. Invariant C3 : elles ne rentrent JAMAIS dans la
    # calibration. On les garde pour le controle croise de ImpliedVol.cpp :
    # un ecart systematique revelerait un bug chez nous.
    "implied_volatility": pl.Float64,
    "theoretical_price": pl.Float64,
    "delta": pl.Float64,
    "gamma": pl.Float64,
    "theta": pl.Float64,
    "vega": pl.Float64,
    "rho": pl.Float64,
}

# Sans ces colonnes, rien n'est calculable : echec immediat.
REQUIRED: set[str] = {
    "contract_symbol",
    "expiration",
    "strike",
    "option_type",
    "bid",
    "ask",
    "underlying_price",
}


class SchemaDriftError(RuntimeError):
    """Le flux amont a change de forme. Echec bruyant, jamais silencieux."""


def enforce_schema(df: pl.DataFrame) -> pl.DataFrame:
    """Valide et normalise la chaine brute.

    Trois niveaux de reaction :
      - colonne obligatoire absente  -> exception
      - colonne optionnelle absente  -> warning, on continue en mode degrade
      - colonne inconnue apparue     -> warning, on ne la conserve pas
    """
    missing = REQUIRED - set(df.columns)
    if missing:
        raise SchemaDriftError(f"colonnes obligatoires absentes : {sorted(missing)}")

    absent = set(RAW_SCHEMA) - set(df.columns)
    if absent:
        log.warning("colonnes optionnelles absentes du flux : %s", sorted(absent))

    extra = set(df.columns) - set(RAW_SCHEMA)
    if extra:
        # Signale une evolution du provider. Ajoute-les a RAW_SCHEMA si utiles :
        # une donnee non ingeree aujourd'hui est perdue pour toujours (C7).
        log.warning("colonnes nouvelles, NON conservees : %s", sorted(extra))

    # strict=False : le flux melange des types (ArrowStringArray, NaN dans les
    # dates). Un cast strict exploserait sur last_trade_time. Le compromis est
    # assume ; le controle de recevabilite ci-dessous rattrape les degats reels.
    out = df.select(
        [pl.col(c).cast(t, strict=False) for c, t in RAW_SCHEMA.items() if c in df.columns]
    )

    # Un cast rate produit des nulls silencieux : on les compte sur les
    # colonnes critiques plutot que de les decouvrir trois semaines plus tard.
    for col in ("strike", "bid", "ask", "expiration"):
        if col in out.columns:
            n_null = out[col].null_count()
            if n_null:
                log.warning("%s : %d valeurs nulles apres cast", col, n_null)

    return out
