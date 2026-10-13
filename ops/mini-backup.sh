#!/bin/bash
# Sauvegarde du Mac mini vers un disque externe.
#
# Pourquoi les garde-fous en tete de script
# -----------------------------------------
# Le piege classique : si le disque externe n'est pas monte, /Volumes/<nom> existe
# quand meme — c'est un dossier vide sur le disque INTERNE. rsync y ecrit alors
# "avec succes", et on croit avoir une sauvegarde. Le disque externe peut aussi etre
# debranche, ou se remplir. Ce script refuse de tourner dans ces cas plutot que de
# produire une fausse sauvegarde.
#
# Ce qu'il fait
# -------------
#   1. verifie que le volume est bien monte, que le marqueur est present, et l'espace libre
#   2. pg_dump -Fc de la base (format custom, restaurable avec pg_restore)
#   3. verifie que le dump est lisible (pg_restore --list), sinon il est marque .invalide
#   4. rsync du bronze Deribit (incremental, append-only, les .part en cours sont ignores)
#   5. purge les dumps au-dela de KEEP_DUMPS jours
#   6. ecrit ~/backup_status.json et ~/backup.log
#
# Installation et planification : voir ops/README.md.

set -u
export PATH="/opt/homebrew/bin:/usr/sbin:/usr/bin:/bin"

REPO="${REPO:-$HOME/vol-surface-engine}"
VOLUME="${VSE_BACKUP_VOLUME:-/Volumes/Element1_APFS}"     # surchargeable : sert aux tests
DEST="${VSE_BACKUP_DEST:-$VOLUME/Quant DB}"
MARKER="$VOLUME/.vse-backup-marker"
LOGDIR="$DEST/logs"
STATUS="$HOME/backup_status.json"
LOG="$HOME/backup.log"
KEEP_DUMPS="${KEEP_DUMPS:-14}"
MIN_FREE_GB="${MIN_FREE_GB:-20}"
MARGIN="${MARGIN:-1.2}"        # facteur de securite sur l'espace necessaire

say() { printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG" >&2; }

echec() {
  say "ECHEC: $*"
  python3 - "$STATUS" "$*" <<'PY' 2>/dev/null || true
import json, sys, time
with open(sys.argv[1], "w") as f:
    json.dump({"ok": False, "last_attempt_utc": time.time(), "error": sys.argv[2]}, f, indent=2)
PY
  exit 1
}

# --- 0. rotation du log ---
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt 500000 ]; then
  tail -1000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

say "--- debut de sauvegarde ---"

# --- 1. garde-fous ---
[ -d "$VOLUME" ] || echec "volume absent : $VOLUME (disque debranche ?)"

POINT_MONTAGE="$(df -P "$VOLUME" 2>/dev/null | tail -1 | awk '{print $NF}')"
[ "$POINT_MONTAGE" = "$VOLUME" ] || echec "garde-fou: $VOLUME n'est PAS un point de montage (df renvoie '$POINT_MONTAGE'). Le disque externe n'est pas monte : sans cette protection, la copie se ferait sur le disque interne."

[ -f "$MARKER" ] || echec "garde-fou: marqueur $MARKER absent. Ce fichier identifie le disque ; sans lui, on n'ecrit pas (protection contre le cas 'dossier vide sur le disque interne')."

# Le marqueur porte l'UUID du volume : protege contre un AUTRE disque monte au meme
# chemin (meme modele, meme nom de volume — cas reel).
UUID_ATTENDU="$(awk -F': *' '/^volume_uuid/{print $2}' "$MARKER" | head -1)"
if [ -n "${UUID_ATTENDU:-}" ]; then
  UUID_VU="$(diskutil info "$VOLUME" 2>/dev/null | awk -F': *' '/Volume UUID/{print $2}' | head -1)"
  [ "$UUID_VU" = "$UUID_ATTENDU" ] || echec "garde-fou: l'UUID du volume monte ($UUID_VU) ne correspond pas a celui du marqueur ($UUID_ATTENDU) : ce n'est pas le bon disque."
  say "identite du disque verifiee (uuid $UUID_VU)"
fi

[ -d "$DEST" ] || echec "garde-fou: $DEST n'existe pas sur le disque externe"

# espace necessaire : ce qu'on doit copier, plus une marge
TAILLE_SOURCE_KB="$(du -sk "$REPO/data/deribit/bronze" 2>/dev/null | awk '{print $1}')"
EGAL_TAILLE="$(du -sk "$DEST/bronze" 2>/dev/null | awk '{print $1}')"
BESOIN_KB="$(awk -v s="${TAILLE_SOURCE_KB:-0}" -v m="$MARGIN" 'BEGIN{printf "%d", s*m}')"
LIBRE_KB="$(df -Pk "$DEST" | tail -1 | awk '{print $4}')"
if [ "$LIBRE_KB" -lt $(( MIN_FREE_GB * 1024 * 1024 )) ]; then
  echec "garde-fou: moins de ${MIN_FREE_GB} Go libres sur $DEST ($(df -h "$DEST" | tail -1 | awk '{print $4}'))"
fi
if [ "$LIBRE_KB" -lt "$BESOIN_KB" ]; then
  echec "garde-fou: espace insuffisant (besoin ~$((BESOIN_KB/1024/1024)) Go, libre $((LIBRE_KB/1024/1024)) Go)"
fi
say "volume ok : $DEST | libre $(df -h "$DEST" | tail -1 | awk '{print $4}') | bronze source $((TAILLE_SOURCE_KB/1024)) Mo | deja sauvegarde $((EGAL_TAILLE/1024)) Mo"

mkdir -p "$LOGDIR" "$DEST/bronze" "$DEST/db"

# --- 2. dump de la base ---
HORODATE="$(date -u '+%Y-%m-%d')"
DUMP="$DEST/db/volsurface-$HORODATE.dump"
T0="$(date +%s)"
say "pg_dump -> $DUMP"
if ! ( cd "$REPO" && docker compose exec -T db pg_dump -Fc -U vse -d volsurface ) > "$DUMP.part" 2> "$LOGDIR/pg_dump-$HORODATE.err"; then
  rm -f "$DUMP.part"
  echec "pg_dump a echoue (voir $LOGDIR/pg_dump-$HORODATE.err)"
fi
mv "$DUMP.part" "$DUMP"
TAILLE_DUMP="$(stat -f%z "$DUMP")"
say "dump ecrit : $((TAILLE_DUMP/1024)) Ko"

# --- 3. le dump est-il lisible ? un dump non verifie n'est pas un dump ---
if ( cd "$REPO" && docker compose exec -T db pg_restore --list ) < "$DUMP" > /dev/null 2>&1; then
  say "dump verifie (pg_restore --list ok)"
else
  mv "$DUMP" "$DUMP.invalide"
  echec "dump illisible : renomme en $DUMP.invalide"
fi

# --- 4. bronze (incremental ; les .part en cours d'ecriture sont ignores) ---
say "rsync du bronze..."
if rsync -a --exclude '*.part' --stats "$REPO/data/deribit/bronze/" "$DEST/bronze/" >> "$LOGDIR/rsync-$HORODATE.log" 2>&1; then
  FICHIERS="$(find "$DEST/bronze" -name '*.jsonl.zst' | wc -l | tr -d ' ')"
  say "bronze synchronise : $FICHIERS fichiers, $(du -sh "$DEST/bronze" | awk '{print $1}')"
else
  echec "rsync du bronze a echoue (voir $LOGDIR/rsync-$HORODATE.log)"
fi

# --- 5. purge des vieux dumps ---
PURGES="$(find "$DEST/db" -name 'volsurface-*.dump' -mtime "+$KEEP_DUMPS" -print -delete | wc -l | tr -d ' ')"
[ "$PURGES" -gt 0 ] && say "purge : $PURGES dump(s) de plus de $KEEP_DUMPS jours"

# --- 6. etat, pour le monitoring ---
DUREE="$(( $(date +%s) - T0 ))"
python3 - "$STATUS" "$DUREE" "$TAILLE_DUMP" "$FICHIERS" "$DEST" <<'PY'
import json, sys, time
duree, taille, fichiers, dest = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
with open(sys.argv[1], "w") as f:
    json.dump({
        "ok": True,
        "last_success_utc": time.time(),
        "last_success_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_s": duree,
        "dump_bytes": taille,
        "bronze_files": fichiers,
        "destination": dest,
    }, f, indent=2)
PY
say "--- sauvegarde terminee en ${DUREE}s ---"
cat "$STATUS" >&2
exit 0
