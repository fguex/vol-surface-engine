"""Ecritures en base.

Aucune connexion n'est ouverte ici : chaque fonction recoit une connexion.
C'est ce qui permet a l'orchestrateur de regrouper plusieurs ecritures dans
UNE transaction -- le coeur de l'invariant de lignage (I5).
"""

from __future__ import annotations

import logging

import polars as pl
import psycopg

log = logging.getLogger(__name__)

# Colonnes de quote_curated, dans l'ordre du COPY.
CURATED_COLS = [
    "observed_at",
    "snapshot_id",
    "underlying",
    "valid_date",
    "contract_root",
    "is_adjusted",
    "expiry",
    "expiry_datetime",
    "t_years",
    "strike",
    "is_call",
    "exercise_type",
    "quote_time",
    "bid",
    "ask",
    "bid_size",
    "ask_size",
    "volume",
    "open_interest",
    "underlying_bid",
    "underlying_ask",
    "last_trade_price",
    "last_trade_time",
    "iv_cboe",
    "delta_cboe",
    "theoretical_price_cboe",
    "reject_reason",
]


def insert_snapshot(conn: psycopg.Connection, meta: dict) -> bool:
    """Retourne True si la ligne a ete inseree, False si elle existait deja.
    C'est ce bool qui porte l'idempotence : si False, copy_curated est saute."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO snapshot (snapshot_id, provider, underlying, valid_date,
                                  capture_slot, observed_at, spot, n_rows_raw, raw_path)
            VALUES (%(snapshot_id)s, %(provider)s, %(underlying)s, %(valid_date)s,
                    %(capture_slot)s, %(observed_at)s, %(spot)s,
                    %(n_rows_raw)s, %(raw_path)s)
            ON CONFLICT (provider, underlying, valid_date, capture_slot) DO NOTHING
            """,
            meta,
        )
        return cur.rowcount == 1


def copy_curated(conn: psycopg.Connection, df: pl.DataFrame) -> int:
    """COPY plutot que des INSERT : inserer 20 000 cotations une par une prend
    des dizaines de secondes, un COPY une fraction de seconde."""
    if df.height == 0:
        return 0

    # Les colonnes absentes du flux (expiry_datetime, quote_time, underlying_bid...)
    # sont ajoutees en NULL : le schema SQL les declare nullables exactement
    # pour ca.
    missing = [c for c in CURATED_COLS if c not in df.columns]
    if missing:
        df = df.with_columns([pl.lit(None).alias(c) for c in missing])

    cols = ",".join(CURATED_COLS)
    with conn.cursor() as cur:
        with cur.copy(f"COPY quote_curated ({cols}) FROM STDIN") as cp:
            for row in df.select(CURATED_COLS).iter_rows():
                cp.write_row(row)
    return df.height


def previous_row_count(
    conn: psycopg.Connection, provider: str, underlying: str
) -> int | None:
    """Volumetrie du dernier snapshot reussi, pour le controle de recevabilite.
    PAR SOUS-JACENT : un seuil global ne verrait pas un SPX tronque un jour ou
    AAPL a reussi."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT n_rows_raw FROM snapshot
            WHERE provider = %s AND underlying = %s
            ORDER BY capture_slot DESC LIMIT 1
            """,
            (provider, underlying),
        )
        row = cur.fetchone()
    return row[0] if row else None


def insert_job_run(conn: psycopg.Connection, meta: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO job_run (run_id, job_name, started_at, finished_at,
                                 status, rows_in, rows_out, error_message)
            VALUES (%(run_id)s, %(job_name)s, %(started_at)s, %(finished_at)s,
                    %(status)s, %(rows_in)s, %(rows_out)s, %(error_message)s)
            ON CONFLICT (run_id) DO UPDATE SET
                finished_at   = EXCLUDED.finished_at,
                status        = EXCLUDED.status,
                rows_in       = EXCLUDED.rows_in,
                rows_out      = EXCLUDED.rows_out,
                error_message = EXCLUDED.error_message
            """,
            meta,
        )
