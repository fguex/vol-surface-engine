#!/bin/bash
# Visite guidee de la base : ce qu'elle contient, table par table, avec de vraies
# lignes. A relancer quand on veut voir l'etat courant.
#
#   ~/vol-surface-engine/ops/db-tour.sh
#
# Ne modifie rien : uniquement des SELECT. La base ne contient que Deribit
# (BTC/ETH) ; le brut reste dans le bronze, sur disque.
#
# Les objets vivent dans les schemas silver (tables) et gold (vues) : les requetes
# ci-dessous les nomment sans prefixe, ce que le search_path de la base resout.
set -u
export PATH="/opt/homebrew/bin:/usr/sbin:/usr/bin:/bin"
cd "${REPO:-$HOME/vol-surface-engine}"

PSQL()   { docker compose exec -T db psql -U vse -d volsurface -P pager=off -q "$@"; }
ESPOT()  { PSQL -tAc "SELECT round(p.index_price::numeric, 2) FROM perp_tick p
                        JOIN instrument i USING (instrument_sk)
                       WHERE i.instrument_name = '$1' ORDER BY p.observed_at DESC LIMIT 1" | tr -d '[:space:]'; }
TITRE()  { printf '\n\033[1m%s\033[0m\n' "$*"; }

TITRE "0. Les couches (bronze / silver / gold)"
PSQL -c "
SELECT n.nspname AS couche,
       CASE c.relkind WHEN 'r' THEN 'table' ELSE 'vue' END AS type,
       string_agg(c.relname, ', ' ORDER BY c.relname) AS objets
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r','v') AND n.nspname IN ('bronze','silver','gold','public')
 GROUP BY n.nspname, c.relkind ORDER BY 1, 2;"

TITRE "1. Vue d'ensemble"
PSQL -c "
SELECT objet, lignes, sur_disque FROM (
  SELECT 'instrument' AS objet, count(*) AS lignes, 1 AS ordre,
         pg_size_pretty(pg_total_relation_size('instrument')) AS sur_disque FROM instrument
  UNION ALL SELECT 'option_mark', count(*), 2, pg_size_pretty(pg_total_relation_size('option_mark')) FROM option_mark
  UNION ALL SELECT 'perp_tick',   count(*), 3, pg_size_pretty(pg_total_relation_size('perp_tick')) FROM perp_tick
  UNION ALL SELECT 'vol_index',   count(*), 4, pg_size_pretty(pg_total_relation_size('vol_index')) FROM vol_index
  UNION ALL SELECT 'schema_migrations', count(*), 5, pg_size_pretty(pg_total_relation_size('schema_migrations')) FROM schema_migrations
) c ORDER BY ordre;"

TITRE "2. Couverture temporelle"
PSQL -c "
SELECT 'option_mark' AS objet, min(observed_at) AS premier, max(observed_at) AS dernier,
       count(DISTINCT date_trunc('hour', observed_at)) AS heures,
       count(DISTINCT observed_at) AS instantanes
  FROM option_mark
UNION ALL
SELECT 'perp_tick', min(observed_at), max(observed_at),
       count(DISTINCT date_trunc('hour', observed_at)), count(DISTINCT observed_at)
  FROM perp_tick
UNION ALL
SELECT 'vol_index', min(observed_at), max(observed_at),
       count(DISTINCT date_trunc('hour', observed_at)), count(DISTINCT observed_at)
  FROM vol_index;"

TITRE "3. Dimension instrument (SCD2 : valid_from / valid_to)"
echo "-- versions courantes :"
PSQL -c "
SELECT instrument_name, kind, option_type AS type, strike, expiry,
       valid_from, coalesce(valid_to::text, '(en cours)') AS valid_to
  FROM instrument WHERE valid_to IS NULL
 ORDER BY valid_from DESC, instrument_name LIMIT 4;"
echo "-- versions fermees : des instruments nes dans le bronze de la veille et expires depuis"
PSQL -c "
SELECT instrument_name, strike, expiry, valid_from, valid_to
  FROM instrument WHERE valid_to IS NOT NULL
 ORDER BY valid_to DESC LIMIT 4;"

echo
echo "spot BTC (index, dernier perp) : $(ESPOT BTC-PERPETUAL)   spot ETH : $(ESPOT ETH-PERPETUAL)"

TITRE "4. La chaine d'options, echeance la plus proche, BTC (derniere valeur connue par instrument)"
PSQL -c "
WITH d AS (
  SELECT DISTINCT ON (m.instrument_sk)
         m.instrument_sk, m.mark_iv, m.mark_price, m.delta, m.open_interest
    FROM option_mark m ORDER BY m.instrument_sk, m.observed_at DESC)
SELECT i.instrument_name, i.expiry, i.strike,
       round(d.mark_iv::numeric, 2)       AS mark_iv,
       d.mark_price,
       round(d.delta::numeric, 4)         AS delta,
       round(d.open_interest::numeric, 1) AS open_interest
  FROM d JOIN instrument i USING (instrument_sk)
 WHERE i.underlying = 'BTC' AND i.option_type = 'P'
   AND i.expiry = (SELECT min(expiry) FROM instrument
                    WHERE underlying = 'BTC' AND kind = 'option' AND expiry > current_date)
 ORDER BY i.strike;"

TITRE "5. Qui porte quoi : couverture des colonnes de option_mark"
PSQL -c "
SELECT count(*) AS lignes,
       count(mark_iv) AS avec_iv,
       count(delta)   AS avec_greeks,
       count(bid_iv)  AS avec_bid_ask_iv,
       count(open_interest) AS avec_open_interest,
       round(100.0 * count(delta) / count(*), 1) AS pct_bande_atm
  FROM option_mark;"

TITRE "6. Perpetuel : funding et open interest (8 derniers points)"
PSQL -c "
SELECT p.observed_at, i.instrument_name, p.index_price, p.mark_price,
       round((10000 * p.current_funding)::numeric, 3) AS funding_bp,
       round(p.open_interest::numeric, 0) AS open_interest
  FROM perp_tick p JOIN instrument i USING (instrument_sk)
 ORDER BY p.observed_at DESC LIMIT 8;"

TITRE "7. Indices de volatilite (DVOL)"
PSQL -c "
SELECT observed_at, underlying, round(volatility::numeric, 2) AS dvol
  FROM vol_index ORDER BY observed_at DESC, underlying LIMIT 6;"

TITRE "8. Instruments par echeance (les 8 prochaines, BTC, versions courantes)"
PSQL -c "
SELECT expiry, count(*) FILTER (WHERE option_type = 'C') AS calls,
       count(*) FILTER (WHERE option_type = 'P') AS puts,
       min(strike) AS strike_min, max(strike) AS strike_max
  FROM instrument
 WHERE underlying = 'BTC' AND kind = 'option' AND expiry > current_date AND valid_to IS NULL
 GROUP BY expiry ORDER BY expiry LIMIT 8;"

echo
