"""Job d'ingestion : fetch -> curate -> write.

Point d'entree : python -m python.jobs.ingest
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import sys
import uuid

import polars as pl

from python.data.fetch import fetch_option_chain, check_admissible
from python.data.schema import enforce_schema
from python.data.curate import curate, rejection_summary
from python.data.export import export_quotes_csv
from python.db import connection
from python.db.writer import (
    insert_snapshot,
    copy_curated,
    insert_job_run,
    previous_row_count,
)
from python.io.atomic import write_parquet_atomic
from python.jobs.config import UNIVERSE, PROVIDER, CSV_DIR, raw_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


def current_capture_slot(now: dt.datetime, minutes: int = 60) -> dt.datetime:
    """Arrondit vers le bas au creneau PLANIFIE.

    L'heure reelle va dans observed_at, jamais dans la cle d'idempotence.
    Deux executions dans le meme creneau produisent le meme snapshot_id ->
    ON CONFLICT DO NOTHING -> pas de doublon.
    """
    return now.replace(
        minute=(now.minute // minutes) * minutes,
        second=0,
        microsecond=0,
    )


def make_snapshot_id(
    provider: str, underlying: str, valid_date: dt.date, capture_slot: dt.datetime
) -> str:
    """SHA256 tronque a 16 hex : lisible ET deterministe.

    Piege : mettre datetime.now() dans la cle detruirait l'idempotence.
    """
    key = f"{provider}|{underlying}|{valid_date.isoformat()}|{capture_slot.isoformat()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def run_one(conn, ticker: str, cfg, now: dt.datetime) -> tuple[int, int]:
    valid_date = now.date()
    capture_slot = current_capture_slot(now)
    snap_id = make_snapshot_id(PROVIDER, ticker, valid_date, capture_slot)

    log.info("fetch %s …", ticker)
    raw_df, spot, observed_at = fetch_option_chain(cfg.fetch_symbol, provider=PROVIDER)

    prev_n = previous_row_count(conn, PROVIDER, ticker)
    check_admissible(raw_df, underlying=ticker, previous_n=prev_n)

    validated = enforce_schema(raw_df)
    rpath = raw_path(PROVIDER, ticker, valid_date, capture_slot)
    write_parquet_atomic(validated, rpath)

    snap_meta = dict(
        snapshot_id=snap_id,
        provider=PROVIDER,
        underlying=ticker,
        valid_date=valid_date,
        capture_slot=capture_slot,
        observed_at=observed_at,
        spot=spot,
        n_rows_raw=raw_df.height,
        raw_path=str(rpath),
    )

    log.info("curate %s (%d rows raw) …", ticker, raw_df.height)
    curated = curate(
        validated,
        underlying=ticker,
        valid_date=valid_date,
        spot=spot,
        keep_roots=cfg.keep_roots,
        exercise_type=cfg.exercise_type,
        min_days=cfg.min_days,
        max_days=cfg.max_days,
        max_spread_rel=cfg.max_spread_rel,
        max_abs_logm=cfg.max_abs_logm,
        min_size=cfg.min_size,
    )
    log.info("rejection summary:\n%s", rejection_summary(curated))

    curated = curated.with_columns([
        pl.lit(observed_at).alias("observed_at"),
        pl.lit(snap_id).alias("snapshot_id"),
    ])

    csv_dir = CSV_DIR / ticker
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_path = csv_dir / "quotes.csv"
    export_quotes_csv(curated, csv_path)
    log.info("CSV exporte -> %s", csv_path)

    with conn.transaction():
        inserted = insert_snapshot(conn, snap_meta)
        if not inserted:
            log.info("snapshot %s deja present -- skip curated write (idempotence)", snap_id)
            return raw_df.height, 0
        n = copy_curated(conn, curated)
        log.info("inserted %d curated rows for %s", n, ticker)

    return raw_df.height, n


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest option chains into TimescaleDB")
    parser.add_argument("--tickers", nargs="*", default=list(UNIVERSE.keys()))
    args = parser.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    run_id = str(uuid.uuid4())
    started_at = now

    tickers = [t for t in args.tickers if t in UNIVERSE]
    if not tickers:
        log.error("No valid tickers. Available: %s", list(UNIVERSE.keys()))
        sys.exit(1)

    rows_in = rows_out = 0
    error_msg = None
    status = "ok"

    try:
        with connection.connect() as conn:
            for ticker in tickers:
                ri, ro = run_one(conn, ticker, UNIVERSE[ticker], now)
                rows_in += ri
                rows_out += ro
    except Exception as exc:
        log.exception("ingest failed")
        error_msg = str(exc)
        status = "error"
        sys.exit(1)
    finally:
        finished_at = dt.datetime.now(dt.timezone.utc)
        try:
            with connection.connect() as conn:
                insert_job_run(
                    conn,
                    dict(
                        run_id=run_id,
                        job_name="ingest",
                        started_at=started_at,
                        finished_at=finished_at,
                        status=status,
                        rows_in=rows_in,
                        rows_out=rows_out,
                        error_message=error_msg,
                    ),
                )
        except Exception:
            log.warning("could not write job_run telemetry")


if __name__ == "__main__":
    main()
