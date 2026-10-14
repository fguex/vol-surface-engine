#!/bin/bash
# Lance la sauvegarde par ssh sur la machine elle-meme. Ce detour n'est pas un
# caprice : un processus lance par cron n'a PAS l'autorisation macOS d'ecrire sur
# un volume amovible (et sur ce mini, aucune session graphique ne permet de
# l'accorder). Le garde-fou d'ecriture de mini-backup.sh refuse alors, a juste
# titre. Un processus ne par sshd, lui, y a droit : verifie en executant
# reellement le cron (voir ops/README.md).
#
# Si un jour l'autorisation est accordee a cron (Reglages > Confidentialite et
# securite > Acces complet au disque), cette indirection devient inutile.
set -u
export PATH="/opt/homebrew/bin:/usr/sbin:/usr/bin:/bin"

REPO="${REPO:-$HOME/vol-surface-engine}"
exec /usr/bin/ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
     "$(id -un)@localhost" "$REPO/ops/mini-backup.sh $*"
