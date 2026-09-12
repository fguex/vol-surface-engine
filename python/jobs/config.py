"""Configuration du pipeline.

Tout ce qui est specifique a un sous-jacent vit ICI, jamais enfoui dans une
fonction. Ajouter un nom = ajouter une entree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROVIDER = "cboe"
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
RAW_DIR = DATA_DIR / "raw" / "option_chains"
CSV_DIR = DATA_DIR / "csv"          # handoff vers le C++ : sous-dossier par ticker


@dataclass(frozen=True)
class UnderlyingConfig:
    fetch_symbol: str                # symbole envoye au provider
    keep_roots: set[str]             # racines OCC conservees en curation
    exercise_type: str               # HYPOTHESE : CBOE ne le fournit pas
    min_days: int = 7
    max_days: int = 365
    max_spread_rel: float = 0.25
    max_abs_logm: float = 1.2
    min_size: int = 0  # CBOE ne fournit pas bid_size/ask_size de facon fiable


# ---------------------------------------------------------------------------
# SPX : deux racines coexistent dans le flux.
#   SPX   ( 9 248 lignes) -- mensuelles, jusqu'a 2031, reglement a l'OUVERTURE
#   SPXW  (19 914 lignes) -- hebdomadaires, ~10 mois, reglement a la CLOTURE
# On ne garde que SPXW : une seule convention de reglement dans une meme
# surface. Les melanger introduirait un biais systematique de 6h30 que la
# convention ACT/365.25 en jours entiers ne peut pas representer.
# Le brut contient les deux (C7) : le jour ou on saura gerer l'heure de
# reglement, l'historique des mensuelles est deja la.
# ---------------------------------------------------------------------------
UNIVERSE: dict[str, UnderlyingConfig] = {
    "SPX": UnderlyingConfig(
        fetch_symbol="SPX",
        keep_roots={"SPXW"},
        exercise_type="European",
    ),
    "SPY": UnderlyingConfig(
        fetch_symbol="SPY",
        keep_roots={"SPY"},
        exercise_type="American",
    ),
    "AAPL": UnderlyingConfig(
        fetch_symbol="AAPL",
        keep_roots={"AAPL"},
        exercise_type="American",
    ),
}


def raw_path(provider: str, underlying: str, valid_date, capture_slot) -> Path:
    """Chemin DETERMINISTE : meme creneau -> meme fichier -> idempotence."""
    slot = capture_slot.strftime("%Y-%m-%dT%H-%MZ")
    return (
        RAW_DIR
        / f"provider={provider}"
        / f"underlying={underlying}"
        / f"valid_date={valid_date.isoformat()}"
        / f"slot={slot}.parquet"
    )
