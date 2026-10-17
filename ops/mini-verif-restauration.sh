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
DUMP="$(ls -1t "$DEST"/db/volsurface-*.dump 2>/dev/null | head -1)"
if [ -z "${DUMP:-}" ]; then
  say "ECHEC: aucun dump dans $DEST/db"
  ecrire_etat 0 "" "aucun dump sur le disque"
  exit 1
fi
say "dump retenu : $DUMP ($(du -h "$DUMP" | awk '{print $1}'))"

SORTIE="$HOME/verif-restauration-$$.out"
/usr/bin/ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
  "$(id -un)@localhost" "$REPO/ops/restore-dump.sh '$DUMP' $CIBLE" > "$SORTIE" 2>&1
RC=$?
cat "$SORTIE" | tee -a "$LOG" >&2
rm -f "$SORTIE"

VERDICT="$(grep -E '^(VERDICT|pg_restore)' "$LOG" | tail -1)"
if [ "$RC" -eq 0 ]; then
  say "--- restauration de controle OK ---"
  ecrire_etat 1 "$DUMP" "$VERDICT"
  exit 0
fi
say "--- restauration de controle EN ECHEC (code $RC) ---"
ecrire_etat 0 "$DUMP" "${VERDICT:-echec sans verdict}"
exit 1
