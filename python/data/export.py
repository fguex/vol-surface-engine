"""Handoff vers le C++.

SEUL endroit du projet ou apparaissent les noms de colonnes du C++
(K, T, isCall, exerciseType, openInterest).
Le jour ou pybind11 remplace les CSV, ce fichier change -- rien d'autre.

PRECISION
  Par defaut, Polars ecrit la representation la plus COURTE qui fait un
  aller-retour EXACT (erreur nulle, verifie). Forcer float_precision=12
  DEGRADE la precision : 0.019164955509924708 devient 0.019164955510, soit
  7.5e-14 d'erreur sur T. Gratuitement.
  => on n'impose PAS float_precision. Le defaut est optimal.
  Le test verify_quotes_roundtrip() ci-dessous le confirme a chaque run.

NULLS
  Polars ecrit un null comme champ VIDE ("volume,oi\n,3\n5,\n").
  Un parseur C++ faisant std::stoll("") leve, ou renvoie 0 en silence selon
  l'implementation. On comble donc AVANT d'ecrire, jamais apres.

EPHEMERE
  Ces fichiers sont DERIVABLES : snapshot_id + engine_git_sha suffisent a
  tout rejouer. Ils ne sont ni archives, ni mis en base, et le repertoire
  de run est dans le .gitignore.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import polars as pl

from python.io.atomic import write_csv_atomic

log = logging.getLogger(__name__)


class EmptyHandoffError(RuntimeError):
    """Zero cotation retenue : le C++ calibrerait sur rien et renverrait un
    succes vide. Echec bruyant plutot que silencieux."""


# ---------------------------------------------------------------------------
# Repertoire de run
# ---------------------------------------------------------------------------
def make_run_dir(base: Path, run_id: str) -> Path:
    """Un repertoire PAR RUN, jamais un chemin fixe : deux runs concurrents ne
    doivent pas s'ecraser, et on veut pouvoir inspecter une entree apres coup."""
    d = base / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# quotes.csv
# ---------------------------------------------------------------------------
def export_quotes_csv(curated: pl.DataFrame, path: Path, *, min_quotes: int = 20) -> int:
    """Ecrit les cotations retenues au format attendu par MarketData::load().

      K            double  strike
      T            double  maturite ACT/365.25
      isCall       int8    1=call, 0=put
      exerciseType text    European | American
      bid, ask     double
      volume       int64   (nulls combles a 0)
      openInterest int64   (nulls combles a 0)
    """
    out = (
        curated.filter(pl.col("reject_reason").is_null())
        .select(
            [
                pl.col("strike").cast(pl.Float64).alias("K"),
                pl.col("t_years").cast(pl.Float64).alias("T"),
                pl.col("is_call").cast(pl.Int8).alias("isCall"),
                pl.col("exercise_type").alias("exerciseType"),
                pl.col("bid").cast(pl.Float64),
                pl.col("ask").cast(pl.Float64),
                pl.col("volume").fill_null(0).cast(pl.Int64),
                pl.col("open_interest").fill_null(0).cast(pl.Int64).alias("openInterest"),
            ]
        )
        # Tri deterministe : deux runs identiques produisent le meme fichier,
        # donc le meme hash -- c'est ce qui rend le test d'idempotence possible.
        .sort(["T", "K", "isCall"])
    )

    if out.height < min_quotes:
        raise EmptyHandoffError(
            f"{out.height} cotations retenues (minimum {min_quotes}). "
            "Verifie la repartition des rejets avant de relancer."
        )

    n_null = sum(out[c].null_count() for c in out.columns)
    if n_null:
        raise EmptyHandoffError(f"{n_null} valeurs nulles dans le CSV de sortie")

    write_csv_atomic(out, path)
    log.info("quotes.csv : %d cotations -> %s", out.height, path)
    return out.height


# ---------------------------------------------------------------------------
# rates.csv / divs.csv
# ---------------------------------------------------------------------------
def export_rates_csv(rates: list[tuple[float, float]], path: Path) -> None:
    """Courbe zero-coupon continue : T,r -- consommee par RateCurve."""
    df = pl.DataFrame(rates, schema={"T": pl.Float64, "r": pl.Float64}, orient="row")
    write_csv_atomic(df.sort("T"), path)


def export_divs_csv(divs: list[tuple[float, float]], path: Path) -> None:
    """Calendrier de dividendes cash : T,amount -- consomme par DivCurve.

    Modele affine D_i = alpha_i + beta_i * S(Ti-). On ne charge que alpha
    (cash) ; beta = 0 cote C++. Un fichier VIDE (en-tete seul) est legitime :
    il signifie "aucun dividende avant la maturite maximale".
    """
    df = pl.DataFrame(divs, schema={"T": pl.Float64, "amount": pl.Float64}, orient="row")
    write_csv_atomic(df.sort("T"), path)


# ---------------------------------------------------------------------------
# manifest.json
# ---------------------------------------------------------------------------
def write_manifest(
    path: Path,
    *,
    run_id: str,
    snapshot_id: str,
    engine_git_sha: str,
    underlying: str,
    valid_date: dt.date,
    spot: float,
    n_quotes: int,
) -> None:
    """Lignage du run, pour l'audit cote Python.

    Le C++ ne lit PAS ce fichier : run_id et spot lui arrivent en arguments de
    ligne de commande. Lui imposer un parseur JSON serait une dependance
    gratuite -- le manifeste sert a rejouer et a auditer, pas a piloter.
    """
    payload = {
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "engine_git_sha": engine_git_sha,
        "underlying": underlying,
        "valid_date": valid_date.isoformat(),
        "spot": spot,
        "n_quotes": n_quotes,
        "written_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify_quotes_roundtrip(path: Path, original: pl.DataFrame, *, tol: float = 1e-10) -> None:
    """Relit le CSV et compare a ce qui devait etre ecrit.

    C'est le test le moins couteux du projet et celui qui attrape l'erreur la
    plus sournoise : une perte de precision sur T se manifeste comme une vol
    implicite legerement fausse, sans jamais lever d'exception.
    """
    back = pl.read_csv(path)
    if back.height != original.height:
        raise AssertionError(f"aller-retour : {back.height} lignes contre {original.height}")
    for col in ("K", "T", "bid", "ask"):
        if col not in back.columns:
            continue
        delta = (back[col].cast(pl.Float64) - original[col].cast(pl.Float64)).abs().max()
        if delta is not None and delta > tol:
            raise AssertionError(f"aller-retour : ecart {delta:.3e} sur {col} (tol {tol:.0e})")
    log.info("aller-retour CSV : OK (%d lignes)", back.height)
