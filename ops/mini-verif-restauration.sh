#!/bin/bash
# Restauration de controle : restaure pour de vrai le dernier dump, chaque semaine.
#
# Un backup jamais restaure n'est pas un backup, c'est une intention. Ce script ne
# reimplemente rien : il appelle ops/restore-dump.sh (qui connait la sequence
# TimescaleDB mesuree) et se contente de juger son verdict, puis d'ecrire un etat
# que ops/mini-alerte.sh surveille.
#
# Pourquoi passer par ssh localhost : un processus lance par cron n'a pas
# l'autorisation macOS de LIRE le disque amovible. Meme raison que pour la
# sauvegarde (cf. ops/mini-backup-launch.sh).
#
# Usage : ops/mini-verif-restauration.sh
set -u
export PATH="/opt/homebrew/bin:/usr/sbin:/usr/bin:/bin"

REPO="${REPO:-$HOME/vol-surface-engine}"
VOLUME="${VSE_BACKUP_VOLUME:-/Volumes/Element1_APFS}"
DEST="${VSE_BACKUP_DEST:-$VOLUME/Quant DB}"
STATUS="$HOME/verif_restauration_status.json"
LOG="$HOME/verif-restauration.log"
CIBLE="restore_verif"

say() { printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG" >&2; }

ecrire_etat() {   # $1=ok(0|1)  $2=dump  $3=verdict
  python3 - "$STATUS" "$1" "$2" "$3" <<'PY'
import json, sys, time
json.dump({"ok": sys.argv[2] == "1", "last_run_utc": int(time.time()),
           "dump": sys.argv[3], "verdict": sys.argv[4]}, open(sys.argv[1], "w"), indent=2)
PY
}

say "--- debut de la restauration de controle ---"
# La lecture du disque amovible passe par ssh elle aussi : cron n'a que le droit
# de lancer ssh, pas de lire /Volumes. Faire le ls ici casserait sous cron.
SSH="/usr/bin/ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new $(id -un)@localhost"
DUMP="$($SSH "ls -1t '$DEST'/db/volsurface-*.dump 2>/dev/null | head -1")"
if [ -z "${DUMP:-}" ]; then
  say "ECHEC: aucun dump dans $DEST/db"
  ecrire_etat 0 "" "aucun dump sur le disque"
  exit 1
fi
TAILLE="$($SSH "ls -lh '$DUMP' | awk '{print \$5}'")"
say "dump retenu : $DUMP ($TAILLE)"

SORTIE="$HOME/verif-restauration-$$.out"
$SSH "$REPO/ops/restore-dump.sh '$DUMP' $CIBLE --keep --no-compare" > "$SORTIE" 2>&1
RC=$?
cat "$SORTIE" | tee -a "$LOG" >&2
rm -f "$SORTIE"

if [ "$RC" -ne 0 ]; then
  say "--- restauration de controle EN ECHEC (code $RC) ---"
  ecrire_etat 0 "$DUMP" "restauration en echec (code $RC)"
  exit 1
fi

# Le contenu restaure est juge ici, pas par restore-dump.sh : comparer a la
# production n'a de sens que juste apres un dump, et le dump controle a la nuit.
# On verifie donc la coherence interne : des lignes, des migrations, une
# hypertable, et des observations qui datent bien de la veille. Un dump
# techniquement restaure mais vide passerait sinon pour un succes.
cd "$REPO" || exit 1
set -a; . ./.env; set +a
MESURE="$(docker compose exec -T db psql -U vse -d "$CIBLE" -tAc \
  "select count(*), coalesce(max(observed_at)::text,'-'),
          coalesce((max(observed_at) > now() - interval '3 days')::int, 0)
     from silver.option_mark" 2>/dev/null | tr -d '\r')"
LIGNES="$(printf '%s' "$MESURE" | cut -d'|' -f1)"
DERNIER="$(printf '%s' "$MESURE" | cut -d'|' -f2)"
FRAIS="$(printf '%s' "$MESURE" | cut -d'|' -f3)"
MIGR="$(docker compose exec -T db psql -U vse -d "$CIBLE" -tAc \
  "select count(*) from public.schema_migrations" 2>/dev/null | tr -d '[:space:]')"
HYPER="$(docker compose exec -T db psql -U vse -d "$CIBLE" -tAc \
  "select count(*) from timescaledb_information.hypertables" 2>/dev/null | tr -d '[:space:]')"

# la base jetable est refermee dans tous les cas, succes ou non
docker compose exec -T db psql -U vse -d volsurface \
  -c "DROP DATABASE IF EXISTS $CIBLE WITH (FORCE)" >/dev/null 2>&1 || true

say "restaure : $LIGNES lignes option_mark | derniere observation $DERNIER | $MIGR migrations | $HYPER hypertable(s)"

OK=1
RAISON=""
case "${LIGNES:-}" in
  ''|*[!0-9]*) OK=0; RAISON="comptage illisible (${LIGNES:-vide})" ;;
esac
if [ "$OK" = "1" ] && [ "$LIGNES" -le 0 ]; then OK=0; RAISON="option_mark restauree vide"; fi
if [ "$OK" = "1" ] && [ "${MIGR:-0}" -le 0 ]; then OK=0; RAISON="table des migrations vide"; fi
if [ "$OK" = "1" ] && [ "${HYPER:-0}" -le 0 ]; then OK=0; RAISON="aucune hypertable restauree"; fi
if [ "$OK" = "1" ] && [ "${FRAIS:-0}" != "1" ]; then OK=0; RAISON="observations trop anciennes (derniere : $DERNIER)"; fi

if [ "$OK" = "1" ]; then
  say "--- restauration de controle OK ---"
  ecrire_etat 1 "$DUMP" "restauration ok : $LIGNES lignes option_mark, derniere observation $DERNIER"
  exit 0
fi
say "--- restauration de controle EN ECHEC : $RAISON ---"
ecrire_etat 0 "$DUMP" "$RAISON"
exit 1
