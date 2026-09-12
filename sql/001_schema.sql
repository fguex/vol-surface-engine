CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 1. Une ligne par capture. La cle unique porte l'idempotence.
CREATE TABLE snapshot (
    snapshot_id   TEXT PRIMARY KEY,
    provider      TEXT        NOT NULL,
    underlying    TEXT        NOT NULL,
    valid_date    DATE        NOT NULL,
    capture_slot  TIMESTAMPTZ NOT NULL,
    observed_at   TIMESTAMPTZ NOT NULL,
    spot          DOUBLE PRECISION NOT NULL,
    n_rows_raw    INTEGER     NOT NULL,
    raw_path      TEXT        NOT NULL,
    UNIQUE (provider, underlying, valid_date, capture_slot)
);

-- 2. Cotations curees : serie temporelle -> hypertable
CREATE TABLE quote_curated (
    observed_at     TIMESTAMPTZ NOT NULL,
    snapshot_id     TEXT NOT NULL REFERENCES snapshot(snapshot_id),
    underlying      TEXT NOT NULL,
    valid_date      DATE NOT NULL,
    contract_root   TEXT NOT NULL,
    is_adjusted     BOOLEAN NOT NULL,
    expiry          DATE NOT NULL,
    expiry_datetime TIMESTAMPTZ,
    t_years         DOUBLE PRECISION NOT NULL,
    strike          NUMERIC(18,6) NOT NULL,
    is_call         BOOLEAN NOT NULL,
    exercise_type   TEXT NOT NULL,
    quote_time      TIMESTAMPTZ,
    bid             DOUBLE PRECISION,
    ask             DOUBLE PRECISION,
    bid_size        INTEGER,
    ask_size        INTEGER,
    volume          BIGINT,
    open_interest   BIGINT,
    underlying_bid  DOUBLE PRECISION,
    underlying_ask  DOUBLE PRECISION,
    last_trade_price        DOUBLE PRECISION,
    last_trade_time         TIMESTAMPTZ,
    iv_cboe                 DOUBLE PRECISION,
    delta_cboe              DOUBLE PRECISION,
    theoretical_price_cboe  DOUBLE PRECISION,
    reject_reason   TEXT
);
SELECT create_hypertable('quote_curated', 'observed_at');

CREATE UNIQUE INDEX quote_curated_key
  ON quote_curated (snapshot_id, contract_root, expiry, strike, is_call, observed_at);
CREATE INDEX quote_curated_lookup
  ON quote_curated (underlying, valid_date, observed_at DESC);

-- 3. Resultats de calibration
CREATE TABLE calibration_run (
    run_id         TEXT PRIMARY KEY,
    snapshot_id    TEXT NOT NULL REFERENCES snapshot(snapshot_id),
    engine_git_sha TEXT NOT NULL,
    started_at     TIMESTAMPTZ NOT NULL,
    finished_at    TIMESTAMPTZ,
    status         TEXT NOT NULL,
    rho            DOUBLE PRECISION,
    nu             DOUBLE PRECISION,
    eta            DOUBLE PRECISION,
    gamma          DOUBLE PRECISION,
    loss_final     DOUBLE PRECISION,
    n_quotes_used  INTEGER,
    min_g          DOUBLE PRECISION
);

-- 4. Structure par terme : une ligne par echeance calibree
CREATE TABLE calibration_slice (
    run_id          TEXT NOT NULL REFERENCES calibration_run(run_id),
    expiry          DATE NOT NULL,
    t_years         DOUBLE PRECISION NOT NULL,
    theta           DOUBLE PRECISION,
    forward_model   DOUBLE PRECISION,
    forward_implied DOUBLE PRECISION,
    n_quotes        INTEGER,
    rmse            DOUBLE PRECISION,
    min_g           DOUBLE PRECISION,
    PRIMARY KEY (run_id, expiry)
);

-- 5. Residus par cotation : le diagnostic fin
CREATE TABLE quote_fit (
    observed_at  TIMESTAMPTZ NOT NULL,
    run_id       TEXT NOT NULL,
    snapshot_id  TEXT NOT NULL,
    expiry       DATE NOT NULL,
    strike       NUMERIC(18,6) NOT NULL,
    is_call      BOOLEAN NOT NULL,
    k_logm       DOUBLE PRECISION,
    iv_market    DOUBLE PRECISION,
    iv_model     DOUBLE PRECISION,
    price_market DOUBLE PRECISION,
    price_model  DOUBLE PRECISION,
    weight       DOUBLE PRECISION,
    residual     DOUBLE PRECISION
);
SELECT create_hypertable('quote_fit', 'observed_at');

-- 6. Telemetrie de tous les jobs
CREATE TABLE job_run (
    run_id        TEXT PRIMARY KEY,
    job_name      TEXT NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ,
    status        TEXT NOT NULL,
    rows_in       INTEGER,
    rows_out      INTEGER,
    error_message TEXT
);