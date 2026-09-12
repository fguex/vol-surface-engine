"""Appel au provider. AUCUNE logique metier ici.

Ce module ne filtre pas, ne derive pas, ne nettoie pas. Il recupere et il
renvoie. Toute transformation appartient a curate.py -- c'est ce qui garantit
que la curation reste une fonction pure du brut.
"""

from __future__ import annotations

import datetime as dt
import logging

import polars as pl

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """Le provider n'a rien renvoye d'exploitable."""


def fetch_option_chain(
    fetch_symbol: str, provider: str = "cboe"
) -> tuple[pl.DataFrame, float, dt.datetime]:
    """Renvoie (chaine brute, spot, observed_at).

    observed_at est l'horloge murale REELLE du fetch. Elle va en metadonnee,
    jamais dans la cle d'idempotence (invariant C6).
    """
    # Import tardif : openbb met plusieurs secondes a se charger, on ne veut
    # pas payer ce cout a l'import du module.
    from openbb import obb

    observed_at = dt.datetime.now(dt.timezone.utc)
    result = obb.derivatives.options.chains(symbol=fetch_symbol, provider=provider)
    pdf = result.to_df()

    if pdf is None or pdf.empty:
        raise FetchError(f"{fetch_symbol}: chaine vide renvoyee par {provider}")

    # reset_index() : to_df() peut poser un index que pl.from_pandas ignorerait.
    df = pl.from_pandas(pdf.reset_index(drop=False))

    if "underlying_price" not in pdf.columns:
        raise FetchError(f"{fetch_symbol}: underlying_price absent du flux")
    spot = float(pdf["underlying_price"].iloc[0])
    if not spot > 0:
        raise FetchError(f"{fetch_symbol}: spot invalide ({spot})")

    log.info("%s : %d lignes, spot=%.2f", fetch_symbol, df.height, spot)
    return df, spot, observed_at


def check_admissible(
    df: pl.DataFrame, *, underlying: str, previous_n: int | None
) -> None:
    """Controles de recevabilite AVANT ecriture.

    Le controle de volumetrie relative est celui qui rapporte le plus : un
    fetch partiel (timeout, pagination interrompue) renvoie un sous-ensemble
    PARFAITEMENT bien forme. Aucun controle de type ne le detecte. Seule la
    comparaison a l'historique le voit.
    """
    if df.height == 0:
        raise FetchError(f"{underlying}: zero ligne")

    if "strike" in df.columns and (df["strike"].min() or 0) <= 0:
        raise FetchError(f"{underlying}: strike negatif ou nul")

    if previous_n is not None and df.height < 0.5 * previous_n:
        raise FetchError(
            f"{underlying}: volumetrie suspecte -- {df.height} lignes contre "
            f"{previous_n} au snapshot precedent, fetch probablement partiel"
        )
