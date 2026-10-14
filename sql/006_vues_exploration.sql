-- 006 : vues de lecture, pour explorer la base sans reecrire la meme requete.
--
-- Aucune table, aucun stockage : uniquement des SELECT. Une vue ne coute rien
-- tant qu'on ne l'interroge pas, et elle sert les trois clients a la fois
-- (DBeaver, psql, Python) — c'est la raison de les mettre ici plutot que dans
-- les carnets de requetes de chacun.
--
-- A verifier quand le schema bouge : une vue ne casse pas une migration, elle
-- casse a la premiere lecture.

-- ---------------------------------------------------------------------------
-- La surface a jour : un instrument par ligne, derniere valeur connue
-- ---------------------------------------------------------------------------
-- `markprice.options` est un flux INCREMENTAL : une tranche de 5 min ne contient
-- que ce qui a bouge. « La surface maintenant » se lit donc par instrument
-- (derniere observation), jamais par tranche.

CREATE OR REPLACE VIEW v_surface AS
WITH derniere AS (
    SELECT DISTINCT ON (m.instrument_sk)
           m.instrument_sk, m.observed_at, m.mark_iv, m.mark_price,
           m.index_price, m.underlying_price, m.bid_iv, m.ask_iv,
           m.open_interest, m.delta, m.gamma, m.vega, m.theta
      FROM option_mark m
     ORDER BY m.instrument_sk, m.observed_at DESC
)
SELECT i.underlying,
       i.instrument_name,
       i.expiry,
       i.option_type,
       d.observed_at                                        AS observe_le,
       i.strike,
       d.index_price                                        AS index_usd,
       d.underlying_price                                   AS sous_jacent_usd,
       d.mark_iv                                            AS iv_pct,
       d.bid_iv,
       d.ask_iv,
       d.mark_price                                         AS prix_unites,
       d.mark_price * d.index_price                         AS prix_usd,
       ln(i.strike / NULLIF(d.underlying_price, 0))         AS k_logm,
       extract(epoch FROM (((i.expiry + time '08:00') AT TIME ZONE 'UTC')
                           - d.observed_at)) / (365.25 * 86400) AS t_annees,
       d.delta, d.vega, d.open_interest
  FROM derniere d
  JOIN instrument i USING (instrument_sk)
 WHERE i.expiry IS NOT NULL
   AND d.mark_iv IS NOT NULL
   AND d.mark_iv NOT IN (0, 999);   -- 0 = pas de prix, 999 = pas de carnet

COMMENT ON VIEW v_surface IS
    'Derniere valeur connue de chaque option, prete pour un ajustement : k = ln(K/F), T en annees, IV en pourcentage.';
COMMENT ON COLUMN v_surface.iv_pct IS
    'Volatilite implicite en POURCENTAGE (35.61 = 35,61 %). Voir sql/005_unites_volatilite.sql.';
COMMENT ON COLUMN v_surface.prix_unites IS
    'Prime en BTC pour BTC-*, en ETH pour ETH-* : options inverses. prix_usd = prix_unites x index.';
COMMENT ON COLUMN v_surface.k_logm IS
    'ln(K / sous_jacent_usd). Reference = le prix sous-jacent publie par Deribit, PAS un forward implique par la parite call-put : ce dernier viendra avec la couche silver.';
COMMENT ON COLUMN v_surface.t_annees IS
    'Maturite en annees, echeance a 08:00 UTC (convention Deribit), base 365,25 jours.';

-- ---------------------------------------------------------------------------
-- La structure par terme de la vol ATM : une ligne par echeance
-- ---------------------------------------------------------------------------
-- Le point de depart naturel d'une premiere coupe SSVI. Le « strike le plus
-- proche » est un proxy d'ATM ; un vrai ATM (delta 0,5, ou interpole en k) sera
-- plus juste quand la couche silver existera.

CREATE OR REPLACE VIEW v_atm_terme AS
SELECT DISTINCT ON (underlying, expiry)
       underlying,
       expiry,
       t_annees,
       observe_le,
       strike           AS strike_le_plus_proche,
       index_usd,
       iv_pct           AS iv_atm_pct,
       delta            AS delta_du_strike_proche,
       open_interest
  FROM v_surface
 WHERE expiry > current_date
 ORDER BY underlying, expiry, abs(strike - index_usd);

COMMENT ON VIEW v_atm_terme IS
    'Vol ATM par echeance et par sous-jacent (strike le plus proche du spot), pour tracer la structure par terme.';

-- ---------------------------------------------------------------------------
-- Le perpetuel : funding et base
-- ---------------------------------------------------------------------------
-- Le funding est le cout de portage du perp : a comparer au taux sans risque
-- pour savoir si le marche est en contango ou en backwardation.

CREATE OR REPLACE VIEW v_perp_funding AS
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
  FROM perp_tick p
  JOIN instrument i USING (instrument_sk);

COMMENT ON VIEW v_perp_funding IS
    'Funding du perpetuel, en points de base par periode de 8 h et annualise (3 periodes par jour, 365 jours).';
