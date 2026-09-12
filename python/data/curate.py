"""Curation : brut -> cure.

FONCTION PURE. Pas de reseau, pas d'horloge, pas d'aleatoire.
Consequence de l'invariant C5 : tout l'aval doit etre reconstructible en
rejouant cette fonction sur le Parquet brut, des mois plus tard, a l'identique.

Regle absolue (invariant I4) : on ne SUPPRIME jamais une ligne, on la MARQUE.
La difference entre "zero cotation retenue aujourd'hui" et "le provider n'a
rien renvoye" doit rester lisible dans la table.
"""

from __future__ import annotations

import datetime as dt

import polars as pl

# Invariant C1 : DateUtils.cpp fait days / 365.25 sur des JOURS ENTIERS.
# Toute divergence ici decale la vol implicite de quelques points de base --
# assez petit pour passer inapercu, assez grand pour fausser la calibration.
ACT = 365.25

# Format OCC : ROOT + YYMMDD(6) + C/P(1) + strike x1000 sur 8 chiffres.
# La racine est de longueur VARIABLE (SPX, SPXW, AAPL), donc on decoupe par la
# droite via le suffixe, jamais par position depuis la gauche.
OCC_SUFFIX_RE = r"\d{6}[CP]\d{8}$"


def curate(
    raw: pl.DataFrame,
    *,
    underlying: str,
    valid_date: dt.date,
    spot: float,
    keep_roots: set[str],
    exercise_type: str,
    min_days: int = 7,
    max_days: int = 365,
    max_spread_rel: float = 0.25,
    max_abs_logm: float = 1.2,
    min_size: int = 1,
) -> pl.DataFrame:
    """Derive les colonnes metier et marque les rejets.

    exercise_type est une HYPOTHESE documentee : CBOE ne fournit pas le style
    d'exercice. Elle vient de UNIVERSE, pas d'une constante enfouie.
    """
    mid = (pl.col("bid") + pl.col("ask")) / 2

    out = raw.with_columns(
        [
            pl.lit(underlying).alias("underlying"),
            pl.lit(valid_date, dtype=pl.Date).alias("valid_date"),
            pl.lit(exercise_type).alias("exercise_type"),
            # CBOE ne signale pas les contrats ajustes. Colonne conservee pour
            # le jour ou on saura les detecter (parsing de racine inattendue).
            pl.lit(False).alias("is_adjusted"),
            # Racine : on retire le suffixe OCC.
            pl.col("contract_symbol").str.replace(OCC_SUFFIX_RE, "").alias("contract_root"),
            pl.col("expiration").alias("expiry"),
            (pl.col("option_type").str.to_lowercase() == "call").alias("is_call"),
            # C1 : jours entiers / 365.25.
            (
                (pl.col("expiration") - pl.lit(valid_date, dtype=pl.Date)).dt.total_days()
                / ACT
            ).alias("t_years"),
            # Colonnes techniques, supprimees en fin de fonction.
            mid.alias("_mid"),
            ((pl.col("ask") - pl.col("bid")) / mid).alias("_spread"),
            (pl.col("strike") / spot).log().abs().alias("_logm"),
        ]
    )

    out = out.with_columns(
        # --- 1. EXCLUSIONS : le contrat n'est pas modelisable --------------
        # En TETE, car un contrat ajuste ou d'une mauvaise racine est
        # parfaitement bien cote et actif : il passerait tous les filtres
        # de liquidite et empoisonnerait le fit silencieusement.
        pl.when(pl.col("is_adjusted"))
        .then(pl.lit("adjusted_contract"))
        .when(~pl.col("contract_root").is_in(sorted(keep_roots)))
        .then(pl.lit("wrong_root"))
        # --- 2. QUALITE : le contrat est bon, la cotation ne l'est pas -----
        .when(pl.col("bid").is_null() | (pl.col("bid") <= 0))
        .then(pl.lit("no_bid"))
        .when(pl.col("ask").is_null() | (pl.col("ask") <= pl.col("bid")))
        .then(pl.lit("crossed_quote"))
        .when(pl.col("_spread") > max_spread_rel)
        .then(pl.lit("spread_too_wide"))
        .when(
            (pl.col("bid_size").fill_null(0) < min_size)
            | (pl.col("ask_size").fill_null(0) < min_size)
        )
        .then(pl.lit("no_depth"))
        .when(
            (pl.col("volume").fill_null(0) == 0)
            & (pl.col("open_interest").fill_null(0) == 0)
        )
        .then(pl.lit("no_activity"))
        # --- 3. DOMAINE : hors du perimetre de calibration -----------------
        .when(pl.col("t_years") < min_days / ACT)
        .then(pl.lit("expiry_too_short"))
        .when(pl.col("t_years") > max_days / ACT)
        .then(pl.lit("expiry_too_long"))
        .when(pl.col("_logm") > max_abs_logm)
        .then(pl.lit("far_otm"))
        .otherwise(None)
        .alias("reject_reason")
    )

    # NOTE : pas de filtre 'stale_quote'. last_trade_time est l'horodatage de
    # la derniere TRANSACTION, pas de la cotation, et il est massivement nul.
    # L'information de fraicheur n'existe pas dans ce flux ; le proxy est la
    # presence d'un marche a deux faces avec de la taille (no_bid + no_depth).

    return out.drop(["_mid", "_spread", "_logm"])


def rejection_summary(curated: pl.DataFrame) -> pl.DataFrame:
    """Repartition des motifs de rejet. A journaliser a chaque run :
    une rupture de pente precede toujours la degradation du fit."""
    return (
        curated.group_by(pl.col("reject_reason").fill_null("kept"))
        .len()
        .sort("len", descending=True)
    )
