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
#   2. rsync du bronze Deribit (incremental : seuls les nouveaux fichiers de l'heure passent)
#   3. pg_dump -Fc de la base, puis verification que le dump est lisible (pg_restore --list)
#   4. purge les dumps au-dela de KEEP_DUMPS jours
#   5. ecrit ~/backup_status.json et ~/backup.log
#
# Deux rythmes, parce que les deux objets n'ont pas la meme nature :
#   --bronze-only   toutes les heures : le bronze grandit en continu, quelques Mo a copier
#   (defaut)        une fois par jour : bronze + dump de la base (petit, et c'est la
#                   reference restaurable ; le dupliquer 24 fois par jour n'apporte rien)
#
# Installation et planification : voir ops/README.md.

set -u
export PATH="/opt/homebrew/bin:/usr/sbin:/usr/bin:/bin"

# Options : --bronze-only (copie le bronze, pas la base) ; --keep-dumps N
BRONZE_ONLY=0
for a in "$@"; do
  case "$a" in
    --bronze-only) BRONZE_ONLY=1 ;;
    *) echo "option inconnue : $a" >&2; exit 2 ;;
  esac
done

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

# Garde-fou d'ECRITURE. Les controles precedents ne lisent que des metadonnees
# (df, presence de fichiers) : un volume monte en lecture seule, ou une
# autorisation macOS refusee a ce processus, passe tous les tests et fait echouer
# rsync en une seconde — vecu cinq heures de suite, avec un message d'erreur
# ecrit... sur le volume injoignable, donc perdu. On tranche tout de suite.
if ! { : > "$DEST/.vse-ecriture" ; } 2>/dev/null; then
  echec "garde-fou: le volume est monte et porte le marqueur, mais l'ECRITURE est refusee. Verifier: volume monte en lecture seule, ou autorisation 'Volumes amovibles' non accordee au processus (Reglages > Confidentialite et securite)."
fi
rm -f "$DEST/.vse-ecriture"

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

HORODATE="$(date -u '+%Y-%m-%d')"
T0="$(date +%s)"
FICHIERS=0
TAILLE_DUMP=0
MODE="complet"
[ "$BRONZE_ONLY" = "1" ] && MODE="bronze"

# --- 2. bronze d'abord : c'est lui qui grandit en continu, quelques Mo a copier par heure.
#        Les .part en cours d'ecriture sont exclus : ils partiront une fois fermes, donc
#        aucun fichier incomplet ne se retrouve sur la sauvegarde.
say "rsync du bronze..."
# Le detail part dans le journal du disque, mais l'ERREUR va dans $HOME : un
# message ecrit sur le volume qu'on n'arrive pas a ecrire ne sert a rien.
RSYNC_DETAIL="$HOME/backup-rsync-$HORODATE.log"
if rsync -a --exclude '*.part' --stats "$REPO/data/deribit/bronze/" "$DEST/bronze/" \
     > "$RSYNC_DETAIL" 2>&1; then
  cat "$RSYNC_DETAIL" >> "$LOGDIR/rsync-$HORODATE.log"
  FICHIERS="$(find "$DEST/bronze" -name '*.jsonl.zst' | wc -l | tr -d ' ')"
  say "bronze synchronise : $FICHIERS fichiers, $(du -sh "$DEST/bronze" | awk '{print $1}')"
else
  say "detail rsync ($RSYNC_DETAIL) :"
  tail -5 "$RSYNC_DETAIL" | while IFS= read -r l; do say "   $l"; done
  echec "rsync du bronze a echoue (detail : $RSYNC_DETAIL, journal du disque : $LOGDIR/rsync-$HORODATE.log)"
fi

# --- 3. dump de la base : mode complet seulement ---
if [ "$BRONZE_ONLY" = "1" ]; then
  say "mode bronze seul : base non dumpee (le dump est quotidien, il n'apporte rien 24 fois par jour)"
else
  DUMP="$DEST/db/volsurface-$HORODATE.dump"
  say "pg_dump -> $DUMP"
  ERR_PG="$HOME/backup-pg_dump-$HORODATE.err"
  if ! ( cd "$REPO" && docker compose exec -T db pg_dump -Fc -U vse -d volsurface ) > "$DUMP.part" 2> "$ERR_PG"; then
    rm -f "$DUMP.part"
    say "detail pg_dump ($ERR_PG) :"
    tail -5 "$ERR_PG" | while IFS= read -r l; do say "   $l"; done
    echec "pg_dump a echoue (detail : $ERR_PG)"
  fi
  mv "$DUMP.part" "$DUMP"
  TAILLE_DUMP="$(stat -f%z "$DUMP")"
  say "dump ecrit : $((TAILLE_DUMP/1024)) Ko"

  # un dump non verifie n'est pas un dump
  if ( cd "$REPO" && docker compose exec -T db pg_restore --list ) < "$DUMP" > /dev/null 2>&1; then
    say "dump verifie (pg_restore --list ok)"
  else
    mv "$DUMP" "$DUMP.invalide"
    echec "dump illisible : renomme en $DUMP.invalide"
  fi

  PURGES="$(find "$DEST/db" -name 'volsurface-*.dump' -mtime "+$KEEP_DUMPS" -print -delete | wc -l | tr -d ' ')"
  [ "$PURGES" -gt 0 ] && say "purge : $PURGES dump(s) de plus de $KEEP_DUMPS jours"
fi

# --- 6. etat, pour le monitoring ---
DUREE="$(( $(date +%s) - T0 ))"
python3 - "$STATUS" "$DUREE" "$TAILLE_DUMP" "$FICHIERS" "$DEST" "$MODE" <<'PY'
import json, os, sys, time

chemin, duree, taille_dump, fichiers, dest, mode = (sys.argv[1], int(sys.argv[2]),
                                                    int(sys.argv[3]), int(sys.argv[4]),
                                                    sys.argv[5], sys.argv[6])
# l'etat est FUSIONNE : une passe horaire ne doit pas effacer la date du dernier dump
etat = {}
if os.path.exists(chemin):
    try:
        with open(chemin) as f:
            etat = json.load(f)
    except Exception:
        etat = {}

now = time.time()
iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
# l'erreur precedente ne doit pas survivre a un succes : sinon l'etat affiche
# "ok": true ET un message d'echec, et plus personne ne sait le lire.
etat.pop("error", None)
etat.update({
    "ok": True,
    "last_success_utc": now,
    "last_success_iso": iso,
    "last_mode": mode,
    "duration_s": duree,
    "bronze_files": fichiers,
    "destination": dest,
})
if mode == "complet":
    etat["last_full_dump_utc"] = now
    etat["last_full_dump_iso"] = iso
    etat["dump_bytes"] = taille_dump
# le bronze est la copie horaire : c'est elle qui doit rester fraiche pour le monitoring
etat["last_bronze_utc"] = now
etat["last_bronze_iso"] = iso
with open(chemin, "w") as f:
    json.dump(etat, f, indent=2)
PY
say "--- sauvegarde terminee en ${DUREE}s ---"
cat "$STATUS" >&2
exit 0
