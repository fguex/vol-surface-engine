#!/usr/bin/env bash
# Tunnel SSH vers la base du mini, pour DBeaver, psql ou les scripts Python.
#
# La base n'ecoute que sur la boucle locale du mini (127.0.0.1:5432) : depuis le
# MacBook il faut donc un tunnel. Celui-ci est independant de DBeaver, ce qui est
# le point : le tunnel SSH integre de DBeaver est en Java, et Java a sa propre
# pile reseau — quand il dit « no route to host » alors que le ssh du terminal
# passe, on ne peut rien diagnostiquer depuis l'interface. Ici, on voit tout.
#
#   scripts/db-tunnel.sh start     ouvre 127.0.0.1:15432 vers le mini:5432
#   scripts/db-tunnel.sh status    est-il ouvert, et sert-il vraiment la base ?
#   scripts/db-tunnel.sh stop      ferme
#
# Cote DBeaver : onglet Main, hote 127.0.0.1, port 15432, PAS d'onglet SSH.

set -euo pipefail

PORT_LOCAL="${PORT_LOCAL:-15432}"
MINI="${MINI:-macmini-ai@macmini-ai.local}"
BASE_DISTANTE="${BASE_DISTANTE:-127.0.0.1:5432}"   # tel que vu DEPUIS le mini

ecoute() { lsof -nP -iTCP:"${PORT_LOCAL}" -sTCP:LISTEN -t 2>/dev/null | head -1; }

demarre() {
    if [ -n "$(ecoute)" ]; then
        echo "deja ouvert (pid $(ecoute)) sur 127.0.0.1:${PORT_LOCAL}"
        return 0
    fi
    # ServerAlive* : sans cela, un tunnel inactif se fait couper en silence par
    # le reseau, et le symptome (DBeaver qui ne repond plus) n'oriente vers rien.
    ssh -f -N \
        -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=6 \
        -L "${PORT_LOCAL}:${BASE_DISTANTE}" "${MINI}"
    sleep 1
    if [ -z "$(ecoute)" ]; then
        echo "ECHEC : rien n'ecoute sur ${PORT_LOCAL}" >&2
        return 1
    fi
    echo "ouvert : 127.0.0.1:${PORT_LOCAL} -> ${MINI}:${BASE_DISTANTE} (pid $(ecoute))"
}

arrete() {
    local pid
    pid="$(ecoute || true)"
    if [ -z "$pid" ]; then
        echo "rien a fermer sur ${PORT_LOCAL}"
        return 0
    fi
    kill "$pid" && echo "ferme (pid ${pid})"
}

etat() {
    local pid
    pid="$(ecoute || true)"
    if [ -z "$pid" ]; then
        echo "ferme : rien n'ecoute sur ${PORT_LOCAL}"
        return 1
    fi
    echo "ouvert : pid ${pid} ecoute sur ${PORT_LOCAL}"
    # Un port en ecoute ne prouve pas que la base repond : on frappe a la porte.
    if command -v psql >/dev/null 2>&1; then
        PGPASSWORD="${PGPASSWORD:-}" psql -h 127.0.0.1 -p "${PORT_LOCAL}" -U vse -d volsurface \
            -tAc 'select 1' >/dev/null 2>&1 \
            && echo "         la base repond" \
            || echo "         le port ecoute mais la base n'a pas repondu (mot de passe ?)"
    elif nc -z -G 3 127.0.0.1 "${PORT_LOCAL}" 2>/dev/null; then
        echo "         TCP ouvert (psql absent : contenu non verifie)"
    fi
}

case "${1:-status}" in
    start)  demarre ;;
    stop)   arrete ;;
    status) etat ;;
    *)      echo "usage: $0 {start|stop|status}" >&2; exit 2 ;;
esac
