#!/usr/bin/env bash
# Bootstrap du Mac mini : outils -> Docker (Colima) -> repo -> smoke test -> collecteur.
#
# Idempotent : on peut le relancer sans risque, chaque etape saute ce qui est deja fait.
# Usage (sur le Mac mini) :
#   bash ~/vol-surface-engine/scripts/macmini_bootstrap.sh
# ou depuis le MacBook, avant le clone :
#   ssh macmini-ai@macmini-ai 'bash -s' < scripts/macmini_bootstrap.sh
#
# Options (variables d'environnement) :
#   SKIP_SMOKE=1     saute le test de 2 minutes
#   COLIMA_CPU=4 COLIMA_MEM=6 COLIMA_DISK=100

set -euo pipefail

REPO_URL="https://github.com/fguex/vol-surface-engine.git"
REPO_DIR="$HOME/vol-surface-engine"
COLIMA_CPU="${COLIMA_CPU:-4}"
COLIMA_MEM="${COLIMA_MEM:-6}"
COLIMA_DISK="${COLIMA_DISK:-100}"

step() { printf '\n==> %s\n' "$*"; }
die()  { printf 'ERREUR: %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------
step "0. Verifications"
OS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
echo "macOS $(sw_vers -productVersion), $(uname -m)"
[ "$(uname -m)" = "arm64" ] || die "attendu arm64"
[ "$OS_MAJOR" -ge 15 ] || echo "ATTENTION: macOS $OS_MAJOR -> Homebrew compilera depuis les sources (lent)"
xcode-select -p >/dev/null 2>&1 || die "Command Line Tools absents : lancer 'xcode-select --install' depuis l'ecran du Mac"
[ -x /opt/homebrew/bin/brew ] || die "Homebrew absent : voir https://brew.sh (necessite le mot de passe admin)"

eval "$(/opt/homebrew/bin/brew shellenv)"
grep -q "brew shellenv" "$HOME/.zprofile" 2>/dev/null \
  || echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> "$HOME/.zprofile"

# --------------------------------------------------------------------------
step "1. Outils Homebrew"
brew update --quiet
for f in git uv colima docker docker-compose docker-buildx; do
  if brew list --formula "$f" >/dev/null 2>&1; then
    echo "  $f : deja installe"
  else
    echo "  $f : installation"
    brew install --quiet "$f"
  fi
done

# Plugins docker (compose, buildx) installes par brew : il faut dire au CLI ou ils sont.
mkdir -p "$HOME/.docker"
CFG="$HOME/.docker/config.json"
python3 - "$CFG" <<'EOF'
import json, os, sys
p = sys.argv[1]
cfg = json.load(open(p)) if os.path.exists(p) and os.path.getsize(p) else {}
d = "/opt/homebrew/lib/docker/cli-plugins"
dirs = cfg.setdefault("cliPluginsExtraDirs", [])
if d not in dirs:
    dirs.append(d)
json.dump(cfg, open(p, "w"), indent=2)
print("  ~/.docker/config.json OK")
EOF

# --------------------------------------------------------------------------
step "2. Colima (moteur Docker sans interface graphique)"
if colima status >/dev/null 2>&1; then
  echo "  colima tourne deja"
else
  colima start --vm-type vz --cpu "$COLIMA_CPU" --memory "$COLIMA_MEM" --disk "$COLIMA_DISK"
fi
# demarrage automatique a l'ouverture de session
brew services list | grep -q "^colima.*started" || brew services start colima
docker context use colima >/dev/null 2>&1 || true
docker run --rm hello-world >/dev/null && echo "  docker run hello-world : OK"
docker compose version

# --------------------------------------------------------------------------
step "3. Repo"
if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"

if [ ! -f .env ]; then
  # mot de passe aleatoire, jamais affiche ni commite (.env est dans .gitignore)
  printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)" > .env
  chmod 600 .env
  echo "  .env cree avec un mot de passe aleatoire"
else
  echo "  .env existe deja, inchange"
fi

uv sync --quiet
echo "  venv : $(.venv/bin/python --version)"

# --------------------------------------------------------------------------
if [ "${SKIP_SMOKE:-0}" != "1" ]; then
  step "4. Smoke test : 2 min de collecte, coupure forcee a 60 s"
  rm -rf data/deribit_smoke
  .venv/bin/python -m python.collect.deribit_ws \
      --duration 120 --rotate 60 --chaos-at 60 --out data/deribit_smoke 2>&1 \
    | grep -E "event (connected|subscribed|disconnected|chaos_close|stop)"
  .venv/bin/python -m python.collect.analyze_bronze data/deribit_smoke
fi

# --------------------------------------------------------------------------
step "5. Collecteur en service (Docker)"
docker compose build collector
docker compose up -d collector

echo "  attente du healthcheck (max 3 min)..."
for _ in $(seq 1 36); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q collector)" 2>/dev/null || echo none)"
  [ "$status" = "healthy" ] && break
  sleep 5
done
docker compose ps collector
[ -f data/deribit/health.json ] && cat data/deribit/health.json && echo
[ "${status:-}" = "healthy" ] || die "collecteur pas healthy : docker compose logs --tail 50 collector"

step "Termine"
cat <<EOF
  Logs en direct      : cd $REPO_DIR && docker compose logs -f collector
  Etat                : docker compose ps collector ; cat data/deribit/health.json
  Rapport de qualite  : .venv/bin/python -m python.collect.analyze_bronze data/deribit
  Mise a jour du code : git pull && docker compose build collector && docker compose up -d collector
  Arret propre        : docker compose stop collector   (jamais 'down -v')
EOF
