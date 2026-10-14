-- 008 : les couches medaillon deviennent visibles DANS la base.
--
-- Jusqu'ici tout vivait dans `public` : impossible de savoir, en regardant la base, ce qui est
-- donnee conformee et ce qui est vue de lecture. Une convention de nommage demanderait au
-- lecteur de la connaitre ; un schema le dit a tout le monde, y compris a un outil graphique
-- qui les affiche en dossiers.
--
--   bronze   les messages bruts de Deribit. PAS ici : ce sont les fichiers zstd de
--            data/deribit/bronze/ (rejouables, ~18 Mo/h). Le schema existe comme repere de
--            vocabulaire, pour que l'absence soit explicite plutot que silencieuse.
--   silver   ce que produit le chargement du bronze (python/jobs/load_marks.py) :
--            instrument (dimension SCD2) + option_mark, perp_tick, vol_index (faits).
--            Une ligne par instrument par tranche de 5 min — un echantillon, pas les ticks.
--   gold     les vues de lecture, sans stockage : l'endroit ou l'on interroge, depuis psql,
--            DBeaver ou Python.
--
-- `schema_migrations` reste dans `public` : ce n'est pas une couche de donnees mais le registre
-- du migrateur, qui doit avoir un emplacement fixe et ne pas dependre d'un search_path.
--
-- Le role recoit `search_path = silver, gold, public` au niveau de la BASE, ce qui evite de
-- casser tout ce qui interroge la base a la main. C'est un filet pour l'exploration, pas une
-- dispense : le code du depot, lui, qualifie les noms (voir load_marks.py, plot_surface.py).

CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

COMMENT ON SCHEMA bronze IS
    'Messages bruts de Deribit : des FICHIERS zstd sous data/deribit/bronze/, pas des tables. Ce schema est un repere de vocabulaire.';
COMMENT ON SCHEMA silver IS
    'Donnee conformee, produite par le chargement du bronze (python/jobs/load_marks.py) : une dimension SCD2 et trois tables de faits, echantillonnees en tranches de 5 min.';
COMMENT ON SCHEMA gold IS
    'Vues de lecture derivees du silver, sans stockage : la couche que l''on interroge.';

-- Le deploiement d'un hypertable d'un schema a l'autre est accepte par TimescaleDB 2.30 :
-- l'identite de l'hypertable suit (verifie : timescaledb_information.hypertables annonce bien
-- silver.option_mark apres coup), et les vues qui le lisaient restent resolues par OID.
ALTER TABLE instrument SET SCHEMA silver;
ALTER TABLE option_mark SET SCHEMA silver;
ALTER TABLE perp_tick  SET SCHEMA silver;
ALTER TABLE vol_index  SET SCHEMA silver;

-- Les vues sont recreees dans gold, cette fois qualifiees. Les ordres de DROP suivent les
-- dependances : v_atm_terme lit v_surface, qui lit v_spot.
DROP VIEW IF EXISTS public.v_atm_terme;
DROP VIEW IF EXISTS public.v_surface;
DROP VIEW IF EXISTS public.v_spot;
DROP VIEW IF EXISTS public.v_perp_funding;

CREATE OR REPLACE VIEW gold.v_spot AS
SELECT DISTINCT ON (i.underlying)
       i.underlying,
       p.observed_at,
       p.index_price      AS index_usd,
       p.mark_price       AS perp_mark_usd,
       p.current_funding
  FROM silver.perp_tick p
  JOIN silver.instrument i USING (instrument_sk)
 ORDER BY i.underlying, p.observed_at DESC;

COMMENT ON VIEW gold.v_spot IS
    'Dernier etat connu du perpetuel par sous-jacent : le spot de reference, et le funding courant.';

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
            ELSE 'index_perpetuel' END                      AS reference_du_k
  FROM derniere d
  JOIN silver.instrument i USING (instrument_sk)
  JOIN gold.v_spot s ON s.underlying = i.underlying
 WHERE i.expiry IS NOT NULL
   AND d.mark_iv IS NOT NULL
   AND d.mark_iv NOT IN (0, 999);

COMMENT ON VIEW gold.v_surface IS
    'Derniere valeur connue de chaque option, prete pour un ajustement : k = ln(K/F), T en annees, IV en pourcentage.';
COMMENT ON COLUMN gold.v_surface.iv_pct IS
    'Volatilite implicite en POURCENTAGE (35.61 = 35,61 %). Voir sql/005_unites_volatilite.sql.';
COMMENT ON COLUMN gold.v_surface.prix_unites IS
    'Prime en BTC pour BTC-*, en ETH pour ETH-* : options inverses. prix_usd = prix_unites x index.';
COMMENT ON COLUMN gold.v_surface.index_usd IS
    'Indice du sous-jacent, lu sur le PERPETUEL (disponible en continu, contrairement aux colonnes de l option).';
COMMENT ON COLUMN gold.v_surface.k_logm IS
    'ln(K / F) avec F = sous_jacent Deribit si publie, sinon l indice du perpetuel. Voir reference_du_k.';
COMMENT ON COLUMN gold.v_surface.reference_du_k IS
    'Quel prix a servi a k_logm : sous_jacent_deribit (propre a l instrument) ou index_perpetuel (repli). Ni l un ni l autre n est un forward implique par la parite call-put : cela viendra avec la couche silver.';
COMMENT ON COLUMN gold.v_surface.t_annees IS
    'Maturite en annees, echeance a 08:00 UTC (convention Deribit), base 365,25 jours.';

CREATE OR REPLACE VIEW gold.v_atm_terme AS
SELECT DISTINCT ON (underlying, expiry)
       underlying,
       expiry,
       t_annees,
       observed_at,
       strike           AS strike_le_plus_proche,
       index_usd,
       iv_pct           AS iv_atm_pct,
       delta            AS delta_du_strike_proche,
       reference_du_k,
       open_interest
  FROM gold.v_surface
 WHERE expiry > current_date
 ORDER BY underlying, expiry, abs(strike - index_usd);

COMMENT ON VIEW gold.v_atm_terme IS
    'Vol ATM par echeance et par sous-jacent (strike le plus proche du spot), pour tracer la structure par terme.';

CREATE OR REPLACE VIEW gold.v_perp_funding AS
SELECT p.observed_at,
       i.instrument_name,
       p.index_price,
       p.mark_price,
       p.mark_price - p.index_price                        AS base_usd,
       p.current_funding,
       round((10000 * p.current_funding)::numeric, 3)      AS funding_bp_8h,
       round((100 * (power(1::double precision + p.current_funding, 365.0 * 3) - 1))::numeric, 2)
                                                           AS funding_annuel_pct,
       p.open_interest
  FROM silver.perp_tick p
  JOIN silver.instrument i USING (instrument_sk);

COMMENT ON VIEW gold.v_perp_funding IS
    'Funding du perpetuel, en points de base par periode de 8 h et annualise (3 periodes par jour, 365 jours).';

-- Le filet pour tout ce qui interroge la base a la main. current_database() plutot que le nom
-- en dur : la migration s'applique ainsi aussi bien a la production qu'aux bases jetables des
-- tests, qui n'ont pas le meme nom.
DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET search_path = silver, gold, public', current_database());
END
$$;
