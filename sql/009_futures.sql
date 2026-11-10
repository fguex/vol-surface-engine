-- 009 : les futures dates, et le forward par echeance.
--
-- Pourquoi (decision D4, 10 nov. 2026) : jusqu'ici k = ln(K / spot), ou "spot" etait
-- l'indice du perpetuel. Or le prix d'exercice se compare au FORWARD de l'echeance,
-- pas au spot : sur une echeance longue, la base (contango/backwardation) se lit
-- comme du skew. Le biais croit avec T.
--
-- Deux changements :
--   1. `instrument` accepte enfin un future date. Le CHECK de 003 imposait
--      `expiry IS NULL` pour tout ce qui n'est pas une option : un future
--      BTC-27MAR26 (echeance, pas de strike) etait donc ininserable.
--   2. Une table de faits `future_mark`, alimentee par le canal ticker.{nom}.agg2,
--      et une vue `gold.v_forward` qui expose la structure par terme.
--
-- A noter : un future Deribit est regle en USD (lineaire), pas en coin. C'est
-- l'option qui est inverse. D'ou is_inverse = false cote future.

-- ---------------------------------------------------------------------------
-- 1. La dimension accepte les futures dates
-- ---------------------------------------------------------------------------

ALTER TABLE silver.instrument DROP CONSTRAINT IF EXISTS instrument_option_complete;

ALTER TABLE silver.instrument ADD CONSTRAINT instrument_option_complete CHECK (
    (kind = 'option'
        AND expiry IS NOT NULL AND strike IS NOT NULL AND option_type IN ('C', 'P'))
    OR (kind = 'future'
        AND expiry IS NOT NULL AND strike IS NULL AND option_type IS NULL)
    OR (kind = 'perpetual'
        AND expiry IS NULL AND strike IS NULL AND option_type IS NULL)
);

-- ---------------------------------------------------------------------------
-- 2. Le fait : le prix du future, par echeance
-- ---------------------------------------------------------------------------
-- Source : ticker.{instrument}.agg2 (agregation 2 s). Le canal .100ms enverrait
-- ~2 Go/jour de plus pour une donnee dont on n'utilise que la valeur de cloture
-- de chaque tranche de 5 min.

CREATE TABLE IF NOT EXISTS silver.future_mark (
    observed_at     timestamptz NOT NULL,
    instrument_sk   bigint      NOT NULL REFERENCES silver.instrument(instrument_sk),
    mark_price      double precision,        -- le forward, en USD
    index_price     double precision,        -- l'indice au meme instant, en USD
    last_price      double precision,
    best_bid_price  double precision,
    best_ask_price  double precision,
    open_interest   double precision,
    PRIMARY KEY (instrument_sk, observed_at)
);

SELECT create_hypertable('silver.future_mark', 'observed_at',
                         chunk_time_interval => INTERVAL '1 day',
                         if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS future_mark_instantane
    ON silver.future_mark (observed_at DESC, instrument_sk);

ALTER TABLE silver.future_mark SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'instrument_sk',
    timescaledb.compress_orderby = 'observed_at DESC'
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM timescaledb_information.jobs
                   WHERE proc_name = 'policy_compression'
                     AND hypertable_name = 'future_mark') THEN
        PERFORM add_compression_policy('future_mark', INTERVAL '7 days');
    END IF;
END $$;

COMMENT ON TABLE silver.future_mark IS
    'Prix des futures dates, par instrument et par instant : la structure par terme du forward.';
COMMENT ON COLUMN silver.future_mark.mark_price IS
    'Prix du future en USD (reglement lineaire), c''est-a-dire le forward de l''echeance.';

-- ---------------------------------------------------------------------------
-- 3. La structure par terme, lisible
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW gold.v_forward AS
WITH derniere AS (
    SELECT DISTINCT ON (m.instrument_sk)
           m.instrument_sk, m.observed_at, m.mark_price, m.index_price,
           m.best_bid_price, m.best_ask_price, m.open_interest
      FROM silver.future_mark m
     ORDER BY m.instrument_sk, m.observed_at DESC
)
SELECT i.underlying,
       i.expiry,
       i.instrument_name,
       d.observed_at,
       d.mark_price                                     AS forward_usd,
       d.index_price                                    AS index_usd,
       extract(epoch FROM (((i.expiry + time '08:00') AT TIME ZONE 'UTC')
                           - d.observed_at)) / (365.25 * 86400) AS t_annees,
       d.open_interest,
       ln(d.mark_price / NULLIF(d.index_price, 0))       AS base_log
  FROM derniere d
  JOIN silver.instrument i USING (instrument_sk)
 WHERE i.kind = 'future'
   AND i.expiry > current_date
   AND d.mark_price IS NOT NULL;

COMMENT ON VIEW gold.v_forward IS
    'Un forward par echeance : le prix du future, sa maturite en annees et la base en log.';
COMMENT ON COLUMN gold.v_forward.base_log IS
    'ln(F / indice) : la base. Positive = contango, negative = backwardation.';

-- ---------------------------------------------------------------------------
-- 4. v_surface : le forward de l'echeance quand il existe
-- ---------------------------------------------------------------------------
-- Deux colonnes ajoutees EN FIN de vue (CREATE OR REPLACE ne peut pas inserer au
-- milieu sans casser les lecteurs) :
--   forward_usd : le forward du future de MEME echeance, NULL sinon.
--   k_fwd       : ln(K / forward_usd), NULL quand il n'y a pas de future.
-- k_logm garde son sens precedent (reference = spot Deribit ou indice du perp),
-- pour ne rien casser ; c'est k_fwd qui devra alimenter la calibration.
--
-- Les echeances d'options sont plus nombreuses que celles des futures (Deribit
-- cote des options quotidiennes). Combler les trous demande une interpolation, qui
-- vit cote moteur (MarketForwardCurve), pas dans une vue SQL.

CREATE OR REPLACE VIEW gold.v_surface AS
WITH derniere AS (
    SELECT DISTINCT ON (m.instrument_sk)
           m.instrument_sk, m.observed_at, m.mark_iv, m.mark_price,
           m.index_price, m.underlying_price, m.bid_iv, m.ask_iv,
           m.open_interest, m.delta, m.gamma, m.vega, m.theta
      FROM silver.option_mark m
     ORDER BY m.instrument_sk, m.observed_at DESC
)
SELECT i.underlying,
       i.instrument_name,
       i.expiry,
       i.option_type,
       d.observed_at,
       i.strike,
       s.index_usd,
       d.underlying_price                                   AS sous_jacent_usd,
       d.mark_iv                                            AS iv_pct,
       d.bid_iv,
       d.ask_iv,
       d.mark_price                                         AS prix_unites,
       d.mark_price * s.index_usd                           AS prix_usd,
       ln(i.strike / NULLIF(coalesce(d.underlying_price, s.index_usd), 0)) AS k_logm,
       extract(epoch FROM (((i.expiry + time '08:00') AT TIME ZONE 'UTC')
                           - d.observed_at)) / (365.25 * 86400) AS t_annees,
       d.delta, d.vega, d.open_interest,
       CASE WHEN d.underlying_price IS NOT NULL THEN 'sous_jacent_deribit'
            ELSE 'index_perpetuel' END                      AS reference_du_k,
       f.forward_usd,
       ln(i.strike / NULLIF(f.forward_usd, 0))              AS k_fwd
  FROM derniere d
  JOIN silver.instrument i USING (instrument_sk)
  JOIN gold.v_spot s ON s.underlying = i.underlying
  LEFT JOIN gold.v_forward f
         ON f.underlying = i.underlying AND f.expiry = i.expiry
 WHERE i.expiry IS NOT NULL
   AND d.mark_iv IS NOT NULL
   AND d.mark_iv NOT IN (0, 999);

COMMENT ON COLUMN gold.v_surface.forward_usd IS
    'Forward du future de meme echeance (gold.v_forward). NULL si Deribit ne cote pas de future sur cette echeance.';
COMMENT ON COLUMN gold.v_surface.k_fwd IS
    'ln(K / forward_usd) : le log-moneyness juste, celui qui doit alimenter la calibration (decision D4).';
