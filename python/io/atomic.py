"""Ecriture atomique.

Aucune dependance metier : ce module ne connait ni les options ni la base.

Pourquoi c'est necessaire (invariant I3) : si le process meurt en cours
d'ecriture directe, on laisse un Parquet TRONQUE que les lectures suivantes
prendront pour valide. os.replace() est atomique sur POSIX -- a aucun instant
un lecteur ne voit un fichier partiel.

Contrainte : le fichier temporaire DOIT etre dans le meme repertoire que la
cible. Un rename entre systemes de fichiers n'est pas atomique.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import polars as pl


def _atomic(path: Path, writer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    try:
        writer(tmp)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_parquet_atomic(df: pl.DataFrame, path: Path) -> None:
    _atomic(path, lambda tmp: df.write_parquet(tmp))


def write_csv_atomic(df: pl.DataFrame, path: Path, float_precision: int | None = None) -> None:
    """Un CSV est du TEXTE, donc les doubles font un aller-retour decimal.

    float_precision=None (defaut) : Polars ecrit la representation la plus
    COURTE qui fait un aller-retour EXACT. C'est optimal -- verifie
    experimentalement, erreur nulle.

    Forcer une precision DEGRADE le resultat : float_precision=12 transforme
    0.019164955509924708 en 0.019164955510, soit 7.5e-14 d'erreur sur T.
    Ne le fixe que si un consommateur exige un format precis.
    """
    _atomic(path, lambda tmp: df.write_csv(tmp, float_precision=float_precision))
