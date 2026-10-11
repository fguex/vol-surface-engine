#!/bin/bash
# Garde-fou de la stack du Mac mini : Colima/Docker -> conteneurs.
#
# Pourquoi ce script existe
# -------------------------
# `brew services start colima` installe un LaunchAgent, qui ne tourne que dans
# une session graphique ouverte. Sur un Mac mini headless (souvent aucune
# session, utilisateur console = root), Colima ne redemarre donc PAS apres un
# reboot : mesure faite le 11 oct. 2026 -> 9 h de ticks Deribit perdus.
# Lancer `brew services` depuis une session SSH echoue de plus avec
# "launchctl enable gui/<uid>/sh.brew.colima -> 125 (domain does not support
# specified action)".
#
# cron, lui, tourne en daemon systeme, independamment de toute session
# graphique, et fonctionne pour cet utilisateur (verifie sur la machine le
# 11 oct. 2026). D'ou ce garde-fou : il est idempotent, il ne fait rien quand
# tout va bien, et il repare sinon en moins de 5 minutes.
#
# Installation (voir ops/README.md) :
#   mkdir -p ~/bin && cp ops/mini-stack-guard.sh ~/bin/ && chmod +x ~/bin/mini-stack-guard.sh
#   ( crontab -l 2>/dev/null | grep -v mini-stack-guard
#     echo "*/5 * * * * /Users/macmini-ai/bin/mini-stack-guard.sh" ) | crontab -
#
# Verification :
#   colima stop && sleep 300 && colima status     # doit revenir tout seul
#   tail -20 ~/stack-guard.log

set -u

export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="${HOME:-/Users/macmini-ai}"
REPO="$HOME/vol-surface-engine"
LOG="$HOME/stack-guard.log"
COLIMA="/opt/homebrew/bin/colima"
DOCKER="/opt/homebrew/bin/docker"

say() { printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$LOG"; }

# Log borne : on garde les 400 dernieres lignes au-dela de 200 Ko.
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt 200000 ]; then
  tail -400 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

# 1. Colima (moteur Docker)
if ! "$COLIMA" status >/dev/null 2>&1; then
  say "colima arrete -> demarrage"
  if "$COLIMA" start >> "$LOG" 2>&1; then
    say "colima demarre"
  else
    say "ECHEC colima start (voir les lignes ci-dessus)"
  fi
fi

# 2. Conteneurs : compose ne relance que ce qui manque. Les deux services ont
#    `restart: unless-stopped`, donc c'est surtout utile apres un demarrage
#    de Colima (les conteneurs sortent a l'arret du VM).
if "$DOCKER" info >/dev/null 2>&1; then
  if [ -d "$REPO" ]; then
    cd "$REPO" || exit 0
    if ! "$DOCKER" compose ps --status running 2>/dev/null | grep -q collector; then
      say "collecteur absent -> docker compose up -d"
      "$DOCKER" compose up -d >> "$LOG" 2>&1 || say "ECHEC compose up -d"
    fi
  fi
else
  say "docker indisponible (colima pas pret)"
fi

exit 0
