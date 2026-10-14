-- 004 : retrait de l'experience CBOE / SPX.
--
-- Contexte : la chaine d'ingestion a d'abord ete construite sur les chaines
-- d'options SPX (source openbb-cboe). Abandonnee le 11 octobre 2026 au profit de
-- Deribit (BTC/ETH, options inverses + perpetuel), qui est la source qui alimente
-- reellement la base depuis 003.
--
-- Le DDL de ces tables vit dans 001_schema.sql, qui N'EST PAS MODIFIEE : le runner
-- (python/db/migrate.py) enregistre le sha256 de chaque migration appliquee et
-- signale toute modification. Une base deja migree et une base neuve doivent donc
-- converger par cette migration, pas par une reecriture de l'histoire.
--
-- DROP TABLE emporte l'hypertable, ses chunks, ses index et les politiques
-- (compression / retention) qui y sont attachees : aucune n'avait ete posee.
-- Ordre : les tables filles avant leurs parents (cles etrangeres vers snapshot).
--
-- Ce qui est CONSERVE : instrument, option_mark, perp_tick, vol_index (Deribit),
-- schema_migrations. Les resultats de calibration (calibration_run, calibration_slice,
-- quote_fit) disparaissent avec le reste : leur forme actuelle est taillee pour des
-- options sur indice (FK vers un snapshot SPX, strikes NUMERIC(18,6) en points,
-- colonnes iv_cboe / delta_cboe). Le schema de calibration sera reecrit pour des
-- options inverses Deribit quand la couche silver/gold arrivera.

DROP TABLE IF EXISTS quote_fit;          -- residus par cotation (hypertable)
DROP TABLE IF EXISTS calibration_slice;  -- structure par terme
DROP TABLE IF EXISTS calibration_run;    -- parametres SSVI calibres
DROP TABLE IF EXISTS quote_curated;      -- cotations curees (hypertable)
DROP TABLE IF EXISTS snapshot;           -- une ligne par capture SPX
DROP TABLE IF EXISTS job_run;            -- telemetrie du job d'ingestion CBOE
