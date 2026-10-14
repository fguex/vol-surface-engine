-- 007 : correction de la reference de prix dans les vues d'exploration.
--
-- 006 prenait le spot dans les colonnes index_price / underlying_price de
-- l'OPTION. Or ces colonnes ne sont remplies que par le canal `ticker`, qui ne
-- suit qu'une bande ATM : passe quelques jours d'echeance, elles sont NULL. Le
-- classement « strike le plus proche du spot » n'avait alors plus rien a quoi se
-- comparer et retombait sur un strike arbitraire. Mesure avant correction :
--
--     BTC 2026-12-25   strike « le plus proche » 30 000   iv_atm 92,86 %
--     BTC 2027-03-26                           94 000            38,25 %
--
-- pour un marche a 83 700. Un chiffre faux, et credible — le plus dangereux.
--
-- Le spot est une propriete du SOUS-JACENT, pas de l'option : il vient du
-- perpetuel (v_spot ci-dessous), alimente en continu pour BTC et ETH. Le prix
-- sous-jacent publie par Deribit reste prefere quand il existe, puisqu'il est
-- propre a l'instrument, et une colonne dit lequel des deux a servi : on doit
-- pouvoir auditer le choix sans relire la vue.

DROP VIEW IF EXISTS v_atm_terme;
DROP VIEW IF EXISTS v_surface;

-- Le marche maintenant, par sous-jacent.
CREATE OR REPLACE VIEW v_spot AS
SELECT DISTINCT ON (i.underlying)
       i.underlying,
       p.observed_at,
       p.index_price      AS index_usd,
       p.mark_price       AS perp_mark_usd,
       p.current_funding
  FROM perp_tick p
  JOIN instrument i USING (instrument_sk)
 ORDER BY i.underlying, p.observed_at DESC;

COMMENT ON VIEW v_spot IS
    'Dernier etat connu du perpetuel par sous-jacent : le spot de reference, et le funding courant.';

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
  JOIN instrument i USING (instrument_sk)
  JOIN v_spot s ON s.underlying = i.underlying
 WHERE i.expiry IS NOT NULL
   AND d.mark_iv IS NOT NULL
   AND d.mark_iv NOT IN (0, 999);

COMMENT ON VIEW v_surface IS
    'Derniere valeur connue de chaque option, prete pour un ajustement : k = ln(K/F), T en annees, IV en pourcentage.';
COMMENT ON COLUMN v_surface.iv_pct IS
    'Volatilite implicite en POURCENTAGE (35.61 = 35,61 %). Voir sql/005_unites_volatilite.sql.';
COMMENT ON COLUMN v_surface.prix_unites IS
    'Prime en BTC pour BTC-*, en ETH pour ETH-* : options inverses. prix_usd = prix_unites x index.';
COMMENT ON COLUMN v_surface.index_usd IS
    'Indice du sous-jacent, lu sur le PERPETUEL (disponible en continu, contrairement aux colonnes de l option).';
COMMENT ON COLUMN v_surface.k_logm IS
    'ln(K / F) avec F = sous_jacent Deribit si publie, sinon l indice du perpetuel. Voir reference_du_k.';
COMMENT ON COLUMN v_surface.reference_du_k IS
    'Quel prix a servi a k_logm : sous_jacent_deribit (propre a l instrument) ou index_perpetuel (repli). Ni l un ni l autre n est un forward implique par la parite call-put : cela viendra avec la couche silver.';
COMMENT ON COLUMN v_surface.t_annees IS
    'Maturite en annees, echeance a 08:00 UTC (convention Deribit), base 365,25 jours.';

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
       reference_du_k,
       open_interest
  FROM v_surface
 WHERE expiry > current_date
 ORDER BY underlying, expiry, abs(strike - index_usd);

COMMENT ON VIEW v_atm_terme IS
    'Vol ATM par echeance et par sous-jacent (strike le plus proche du spot), pour tracer la structure par terme.';
