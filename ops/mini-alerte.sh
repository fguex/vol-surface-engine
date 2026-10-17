#!/bin/bash
# Veille du Mac mini : previent sur le telephone quand quelque chose ne va pas.
#
# Pourquoi ce script existe
# -------------------------
# Le 14 oct. 2026, la sauvegarde quotidienne a echoue des la premiere etape et
# aucun dump n'a ete ecrit. L'information etait dans ~/backup.log, que personne
# ne lit. Un echec silencieux n'est pas une sauvegarde ratee : c'est une
# sauvegarde qu'on CROIT avoir. C'est le pire des trois cas.
#
# Pourquoi il tourne par cron sur l'HOTE, pas dans un conteneur
# ------------------------------------------------------------
# Une des pannes a detecter est l'arret de Colima (deja vecu : 9 h de ticks
# perdus). Un veilleur heberge dans ce qu'il surveille ne peut pas signaler sa
# propre mort.
#
# Pourquoi il ne notifie que sur CHANGEMENT
# -----------------------------------------
# Un probleme qui dure enverrait sinon 144 messages par jour, et plus personne
# ne les lirait. On previent a l'apparition, on insiste toutes les 6 h tant que
# ca dure, et on previent quand c'est repare.
#
# Usage : ops/mini-alerte.sh [--test]
#   --test : envoie une notification immediate (verifie le canal, pas les controles)
set -u
export PATH="/opt/homebrew/bin:/usr/sbin:/usr/bin:/bin"

REPO="${REPO:-$HOME/vol-surface-engine}"
VOLUME="${VSE_BACKUP_VOLUME:-/Volumes/Element1_APFS}"
DEST="${VSE_BACKUP_DEST:-$VOLUME/Quant DB}"
LOG="$HOME/alerte.log"
RAPPEL=21600              # 6 h entre deux rappels sur un probleme qui persiste
MIN_FREE_GB=10            # disque interne
MIN_FREE_EXT_GB=50        # disque de sauvegarde
DUMP_MAX_H=36             # un dump quotidien : 36 h laisse passer une passe ratee
BRONZE_MAX_H=2            # le bronze est copie toutes les heures

cd "$REPO" 2>/dev/null || { echo "repo introuvable : $REPO" >&2; exit 1; }
if [ -f .env ]; then set -a; . ./.env; set +a; fi
TOPIC="${NTFY_TOPIC:-}"
SERVEUR="${NTFY_SERVER:-https://ntfy.sh}"

say() { printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG" >&2; }

notifier() {   # $1=id  $2=qualificatif  $3=message  $4=priorite
  local titre="VSE mini — $1 : $2"
  if [ -z "$TOPIC" ]; then
    say "[NON ENVOYE — NTFY_TOPIC absent du .env] $titre — $3"
    return 0
  fi
  if curl -fsS -m 15 -H "Title: $titre" -H "Priority: $4" -H "Tags: warning" \
        --data-binary "$3" "$SERVEUR/$TOPIC" >/dev/null 2>&1; then
    say "alerte envoyee : $titre — $3"
  else
    say "ECHEC d'envoi de l'alerte ($titre) — reseau, ou topic refuse par $SERVEUR"
  fi
}

if [ "${1:-}" = "--test" ]; then
  notifier "test" "verification du canal" \
    "Si ce message arrive sur ton telephone, les alertes du mini fonctionnent." 3
  exit 0
fi

# ------------------------------------------------------------------ controles
PROBS=""
probleme() { PROBS="${PROBS}$1"$'\t'"$2"$'\n'; }
NOW="$(date +%s)"

# 1. sauvegarde : etat, fraicheur du bronze, fraicheur du dump
if [ ! -f "$HOME/backup_status.json" ]; then
  probleme sauvegarde "aucun etat de sauvegarde (~/backup_status.json absent)"
else
  ETAT_SAUV="$(python3 - "$HOME/backup_status.json" <<'PY' 2>/dev/null || echo "0 0 0"
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("0 0 0"); raise SystemExit
print(int(bool(d.get("ok"))), int(d.get("last_bronze_utc") or 0), int(d.get("last_full_dump_utc") or 0))
PY
)"
  OK_S="${ETAT_SAUV%% *}"
  RESTE="${ETAT_SAUV#* }"
  BRONZE="${RESTE%% *}"
  DUMP="${RESTE##* }"
  [ "$OK_S" = "1" ] || probleme sauvegarde "la derniere passe de sauvegarde est en echec (voir ~/backup.log)"
  if [ "$BRONZE" -gt 0 ] && [ $(( (NOW - BRONZE) / 3600 )) -ge "$BRONZE_MAX_H" ]; then
    probleme sauvegarde "le bronze n'a pas ete copie depuis $(( (NOW - BRONZE) / 3600 )) h"
  fi
  if [ "$DUMP" -eq 0 ]; then
    probleme dump "aucun dump complet n'est enregistre"
  elif [ $(( (NOW - DUMP) / 3600 )) -ge "$DUMP_MAX_H" ]; then
    probleme dump "aucun dump complet depuis $(( (NOW - DUMP) / 3600 )) h"
  fi
fi

# 2. collecteur : le flux arrive-t-il encore ?
H="$REPO/data/deribit/health.json"
if [ ! -f "$H" ]; then
  probleme collecte "health.json absent : le collecteur n'a jamais demarre"
else
  AGE="$(python3 -c 'import json,sys,time; print(int(time.time()-json.load(open(sys.argv[1]))["last_msg_at"]))' "$H" 2>/dev/null || echo 999999)"
  [ "$AGE" -gt 180 ] && probleme collecte "aucun message Deribit depuis $(( AGE / 60 )) min"
fi

# 3. base : la donnee chargee est-elle fraiche ? (la question qui compte n'est pas
#    "le conteneur repond", c'est "est-ce que le chargement a avance")
if docker info >/dev/null 2>&1; then
  N="$(docker compose exec -T db psql -U vse -d volsurface -tAc \
        "select count(*) from silver.option_mark where observed_at > now() - interval '20 minutes'" \
        2>/dev/null | tr -d '[:space:]')"
  if [ -z "${N:-}" ]; then
    probleme base "requete de fraicheur impossible (base injoignable ?)"
  elif [ "$N" -lt 1 ]; then
    probleme base "aucune ligne chargee dans les 20 dernieres minutes"
  fi
else
  probleme docker "Colima / Docker ne repond pas"
fi

# 4. restauration verifiee : la passe hebdomadaire a-t-elle lieu, et reussit-elle ?
V="$HOME/verif_restauration_status.json"
if [ ! -f "$V" ]; then
  probleme restauration "aucune restauration de controle enregistree (le cron du dimanche a-t-il tourne ?)"
else
  RS="$(python3 - "$V" <<'PY' 2>/dev/null || echo "0 0"
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("0 0"); raise SystemExit
print(int(bool(d.get("ok"))), int(d.get("last_run_utc") or 0))
PY
)"
  OK_R="${RS%% *}"
  DERN_R="${RS##* }"
  [ "$OK_R" = "1" ] || probleme restauration "la derniere restauration de controle a echoue (voir ~/verif-restauration.log)"
  if [ "$DERN_R" -gt 0 ] && [ $(( (NOW - DERN_R) / 86400 )) -ge 8 ]; then
    probleme restauration "aucune restauration de controle depuis $(( (NOW - DERN_R) / 86400 )) jours"
  fi
fi

# 5. disques
LIBRE="$(df -Pk / | tail -1 | awk '{print $4}')"
if [ "$LIBRE" -lt $(( MIN_FREE_GB * 1024 * 1024 )) ]; then
  probleme disque "moins de ${MIN_FREE_GB} Go libres en interne ($(df -h / | tail -1 | awk '{print $4}'))"
fi
if [ ! -d "$VOLUME" ] || [ "$(df -P "$VOLUME" 2>/dev/null | tail -1 | awk '{print $NF}')" != "$VOLUME" ]; then
  probleme disque_externe "le disque de sauvegarde n'est pas monte ($VOLUME)"
else
  LI="$(df -Pk "$VOLUME" | tail -1 | awk '{print $4}')"
  if [ "$LI" -lt $(( MIN_FREE_EXT_GB * 1024 * 1024 )) ]; then
    probleme disque_externe "moins de ${MIN_FREE_EXT_GB} Go libres sur le disque de sauvegarde"
  fi
fi

# ------------------------------------------------------- notification, avec etat
# La logique d'etat est en Python : en bash, comparer des identifiants et des
# horodatages dans un fichier tabule a rate un rappel lors du test (une
# iteration sur trois n'a pas notifie). Le Python decrit ici est deterministe et
# se teste en trois passages : apparition, silence, retablissement.
ETAT="$HOME/.alerte_actifs.json"
ACTIONS="$(printf '%s' "$PROBS" | python3 -c '
import json, sys, time
chemin, rappel = sys.argv[1], int(sys.argv[2])
now = int(time.time())
probs = {}
for ligne in sys.stdin.read().splitlines():
    if not ligne.strip():
        continue
    ident, _, msg = ligne.partition("	")
    if ident:
        probs[ident] = msg
try:
    etat = json.load(open(chemin))
except Exception:
    etat = {}
for ident, msg in probs.items():
    dernier = int(etat.get(ident) or 0)
    if dernier == 0:
        print("NOUVEAU	%s	%s" % (ident, msg))
    elif now - dernier > rappel:
        print("RAPPEL	%s	%s" % (ident, msg))
for ident in list(etat):
    if ident not in probs:
        print("REPARE	%s	%s" % (ident, ident))
json.dump({k: now for k in probs}, open(chemin, "w"), indent=2)
' "$ETAT" "$RAPPEL")"

NB=0
while IFS=$'	' read -r TYPE ID MSG; do
  [ -z "${TYPE:-}" ] && continue
  case "$TYPE" in
    NOUVEAU) NB=$(( NB + 1 )); notifier "$ID" "PROBLEME" "$MSG" 4 ;;
    RAPPEL)                  notifier "$ID" "toujours en cours" "$MSG" 3 ;;
    REPARE)                  notifier "$ID" "REPARE" "$MSG est revenu a la normale." 3 ;;
  esac
done <<< "$ACTIONS"

NACTIF="$(printf '%s' "$PROBS" | grep -c . || true)"
if [ "${NACTIF:-0}" -eq 0 ]; then
  say "tout va bien : aucun des controles ne signale de probleme"
else
  say "$NACTIF probleme(s) actif(s), $NB nouvelle(s) alerte(s) — voir ~/alerte.log"
fi
exit 0
