#!/bin/bash
# Teste une sauvegarde : restaure un dump dans une base jetable et compare avec la prod.
#
# Un backup jamais restauré n'est pas un backup. Ce script rend le test reproductible.
#
# La sequence ci-dessous n'est pas devinable, elle a ete mesuree sur TimescaleDB 2.30 :
#   - l'extension timescaledb doit exister DANS la base cible AVANT l'appel a
#     timescaledb_pre_restore(), sinon la fonction n'existe pas (elle est fournie
#     par l'extension, pas par le serveur) ;
#   - avec cette sequence : 0 erreur, 0 avertissement, hypertables, index, cles
#     etrangeres et donnees restaures a l'identique ;
#   - sans elle : 2 erreurs (contrainte de chunk, option ONLY) et la cle etrangere
#     de la table de faits manquante apres restauration.
#
# Usage :
#   ops/restore-dump.sh <fichier.dump> [nom_base_jetable] [--keep]
#   ops/restore-dump.sh "/Volumes/Element1_APFS/Quant DB/db/volsurface-2026-10-13.dump"

set -u
export PATH="/opt/homebrew/bin:/usr/sbin:/usr/bin:/bin"

REPO="${REPO:-$HOME/vol-surface-engine}"
DUMP="${1:-}"
CIBLE="${2:-restore_test}"
GARDER=0
COMPARER=1
for a in "$@"; do
  [ "$a" = "--keep" ] && GARDER=1
  # --no-compare : la comparaison avec la production n'a de sens que juste apres
  # un dump. Sur un dump de la nuit restaure le lendemain midi, la production a
  # grandi : l'ecart est normal, et juger sur lui ferait echouer la restauration
  # de controle chaque semaine. Le controle hebdomadaire juge donc sur l'absence
  # d'erreur et sur le contenu restaure (voir mini-verif-restauration.sh).
  [ "$a" = "--no-compare" ] && COMPARER=0
done

[ -n "$DUMP" ] || { echo "usage: $0 <fichier.dump> [nom_base_jetable] [--keep]" >&2; exit 2; }
[ -f "$DUMP" ] || { echo "dump introuvable : $DUMP" >&2; exit 2; }
if [ "$CIBLE" = "volsurface" ]; then
  echo "refus: la base cible ne peut pas etre la base de production" >&2
  exit 2
fi

cd "$REPO" || exit 1
set -a; . ./.env; set +a
PSQL="docker compose exec -T db psql -U vse"
PG_RESTORE="docker compose exec -T db pg_restore -U vse"

echo "dump    : $DUMP ($(du -h "$DUMP" | awk '{print $1}'))"
echo "cible   : $CIBLE (sera supprimee a la fin)"

$PSQL -d volsurface -c "DROP DATABASE IF EXISTS $CIBLE WITH (FORCE)" >/dev/null 2>&1 || true
$PSQL -d volsurface -c "CREATE DATABASE $CIBLE" >/dev/null || { echo "creation de $CIBLE impossible" >&2; exit 1; }

# 1. l'extension d'abord (sinon pre_restore n'existe pas dans cette base)
$PSQL -d "$CIBLE" -c "CREATE EXTENSION IF NOT EXISTS timescaledb" >/dev/null || exit 1
# 2. signal a Timescale que tout ce qui arrive est une restauration
$PSQL -d "$CIBLE" -tAc "SELECT timescaledb_pre_restore()" >/dev/null || exit 1
# 3. restauration
$PG_RESTORE -d "$CIBLE" --no-owner < "$DUMP" 2>/tmp/vse_restore.err
CODE=$?
ERREURS="$(grep -c 'pg_restore: error' /tmp/vse_restore.err || true)"
AVERTS="$(grep -c 'pg_restore: warning' /tmp/vse_restore.err || true)"
# 4. retour a la normale
$PSQL -d "$CIBLE" -tAc "SELECT timescaledb_post_restore()" >/dev/null || exit 1

echo "pg_restore : exit=$CODE erreurs=$ERREURS avertissements=$AVERTS"
[ "$ERREURS" -gt 0 ] && grep 'pg_restore: error' /tmp/vse_restore.err | sed 's/^/  /'

echo "--- contenu restaure ---"
$PSQL -d "$CIBLE" -tAc "select 'instrument='||(select count(*) from silver.instrument)||' option_mark='||(select count(*) from silver.option_mark)||' migrations='||(select count(*) from public.schema_migrations)"
$PSQL -d "$CIBLE" -tAc "select 'hypertables: '||coalesce(string_agg(hypertable_name, ', '), '(aucune)') from timescaledb_information.hypertables"
echo "--- production (pour comparaison) ---"
$PSQL -d volsurface -tAc "select 'instrument='||(select count(*) from silver.instrument)||' option_mark='||(select count(*) from silver.option_mark)||' migrations='||(select count(*) from public.schema_migrations)"

# 5. la comparaison est le verdict (option_mark : la table de faits la plus grosse)
ATTENDU=""
OBTENU=""
if [ "$COMPARER" = "1" ]; then
  ATTENDU="$($PSQL -d volsurface -tAc 'select count(*) from silver.option_mark' | tr -d '[:space:]')"
  OBTENU="$($PSQL -d "$CIBLE" -tAc 'select count(*) from silver.option_mark' | tr -d '[:space:]')"
fi

if [ "$GARDER" = "0" ]; then
  $PSQL -d volsurface -c "DROP DATABASE $CIBLE" >/dev/null && echo "base de test supprimee"
fi

if [ "$COMPARER" = "0" ]; then
  if [ "$CODE" = "0" ] && [ "$ERREURS" = "0" ]; then
    echo "VERDICT: restauration sans erreur (comparaison avec la production non demandee)"
    exit 0
  fi
  echo "VERDICT: probleme (restauration exit=$CODE, erreurs=$ERREURS)" >&2
  exit 1
fi
if [ "$CODE" = "0" ] && [ "$ERREURS" = "0" ] && [ "$ATTENDU" = "$OBTENU" ]; then
  echo "VERDICT: sauvegarde valide ($OBTENU lignes option_mark, restauration sans erreur)"
  exit 0
fi
echo "VERDICT: probleme (attendu=$ATTENDU obtenu=$OBTENU, exit=$CODE, erreurs=$ERREURS)" >&2
exit 1
