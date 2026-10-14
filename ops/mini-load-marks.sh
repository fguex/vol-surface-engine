#!/bin/bash
# Bronze Deribit -> base : etats echantillonnes de la chaine et du perpetuel.
# Lance par cron toutes les 5 minutes.
#
# Pourquoi un script et pas une ligne de cron : il faut charger le mot de passe de
# la base depuis .env, fixer les variables libpq, et garder une trace de chaque
# passage. Une ligne de cron de cette longueur n'est jamais relue.
set -u
export PATH="/opt/homebrew/bin:/usr/sbin:/usr/bin:/bin"

REPO="${REPO:-$HOME/vol-surface-engine}"
LOG="${LOG:-$HOME/load-marks.log}"
SINCE_HOURS="${SINCE_HOURS:-3}"

cd "$REPO" || exit 1
# shellcheck disable=SC1091
set -a; . ./.env; set +a
export PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE=volsurface PGUSER=vse
export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD absent de .env}"

{
  echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') chargement (depuis ${SINCE_HOURS} h) ==="
  ./.venv/bin/python -m python.jobs.load_marks --since-hours "$SINCE_HOURS"
  echo "exit=$?"
} >> "$LOG" 2>&1

# Le journal ne doit pas grossir sans fin : on garde les 2 000 dernieres lignes.
if [ "$(wc -l < "$LOG" | tr -d ' ')" -gt 4000 ]; then
  tail -2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
