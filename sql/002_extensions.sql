-- 002 : extensions et conventions de schema.
--
-- btree_gist est necessaire aux dimensions bitemporelles (003) : la contrainte
-- d'exclusion EXCLUDE USING gist (cle WITH =, tstzrange(valid_from, valid_to) WITH &&)
-- exige un index gist capable de comparer une egalite sur texte/entier.
-- Verifie disponible dans l'image timescale/timescaledb:2.30.0-pg16 (btree_gist 1.7).

CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Convention de nommage des migrations : NNN_nom.sql, NNN a 3 chiffres minimum,
-- zero-padde, croissant, jamais reutilise. Le runner (python/db/migrate.py) refuse
-- tout autre nom et verifie l'empreinte sha256 de chaque fichier deja applique.
