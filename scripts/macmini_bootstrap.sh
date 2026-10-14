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

# Homebrew peut installer un git compile pour un macOS plus recent : le binaire
# s'execute mais casse sur HTTPS (symbole libcurl absent de l'OS), ce qui fait
# echouer silencieusement "brew update" ... et le clone du repo. On garde le git
# d'Apple (/usr/bin/git) tant que ce n'est pas regle.
if ! git ls-remote --exit-code https://github.com/Homebrew/brew >/dev/null 2>&1; then
  echo "  git du PATH ne fait pas d'HTTPS -> retour au git d'Apple"
  if [ -x /opt/homebrew/bin/git ]; then
    brew uninstall --ignore-dependencies git >/dev/null 2>&1 || true
  fi
  hash -r 2>/dev/null || true
  export PATH="/usr/bin:/bin:$PATH"
  git ls-remote --exit-code https://github.com/Homebrew/brew >/dev/null 2>&1 \
    || die "aucun git fonctionnel en HTTPS (installer Command Line Tools)"
fi
echo "  git : $(command -v git) ($(git --version))"

# --------------------------------------------------------------------------
step "1. Outils Homebrew"
brew update --quiet
for f in git colima docker docker-compose docker-buildx; do
  if brew list --formula "$f" >/dev/null 2>&1; then
    echo "  $f : deja installe"
  else
    echo "  $f : installation"
    brew install --quiet "$f"
  fi
done

# uv : pas de bouteille pour les macOS anciens -> brew le compilerait depuis les
# sources (Rust, plusieurs minutes). L'installeur officiel livre un binaire
# precompile, on le prefere.
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
echo "  uv : $(command -v uv) ($(uv --version))"

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
docker context use colima >/dev/null 2>&1 || true

# Demarrage automatique : brew services installe un LaunchAgent, qui ne tourne
# que dans une session graphique ouverte ; et depuis une session SSH il echoue
# ("launchctl enable gui/<uid>/sh.brew.colima" -> 125, domaine graphique non
# pilotable a distance). Le redemarrage reel est donc assure par le garde-fou
# cron de l'etape 7, qui fonctionne sans session graphique ni sudo.
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
  if [ "$status" = "healthy" ]; then break; fi
  sleep 5
done
docker compose ps collector
[ -f data/deribit/health.json ] && cat data/deribit/health.json && echo
[ "${status:-}" = "healthy" ] || die "collecteur pas healthy : docker compose logs --tail 50 collector"

step "6. Base de donnees (TimescaleDB, volume persistant)"
docker compose up -d db
echo "  attente du healthcheck (max 2 min)..."
for _ in $(seq 1 24); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q db)" 2>/dev/null || echo none)"
  if [ "$status" = "healthy" ]; then break; fi
  sleep 5
done
docker compose ps db
if [ "${status:-}" = "healthy" ]; then
  # Les tables viennent des migrations versionnees, pas de l'entrypoint Docker
  # (qui ne se declenche qu'a la premiere initialisation du volume, sans trace).
  echo "  application des migrations..."
  set -a; . "$REPO_DIR/.env"; set +a
  PGHOST=127.0.0.1 PGUSER=vse PGDATABASE=volsurface PGPASSWORD="$POSTGRES_PASSWORD" \
    PYTHONPATH="$REPO_DIR" "$REPO_DIR/.venv/bin/python" -m python.db.migrate --sql-dir "$REPO_DIR/sql"
  PGHOST=127.0.0.1 PGUSER=vse PGDATABASE=volsurface PGPASSWORD="$POSTGRES_PASSWORD" \
    PYTHONPATH="$REPO_DIR" "$REPO_DIR/.venv/bin/python" -m python.db.migrate --status --sql-dir "$REPO_DIR/sql"
  docker compose exec -T db psql -U vse -d volsurface -tAc \
    "select extname||' '||extversion from pg_extension where extname='timescaledb'"
  docker compose exec -T db psql -U vse -d volsurface -tAc \
    "select hypertable_name from timescaledb_information.hypertables order by 1"
else
  echo "  ATTENTION: db pas healthy -> docker compose logs --tail 50 db"
fi

step "7. Taches planifiees (cron, sans session graphique ni sudo)"
# Les scripts cron pointent sur le REPO, pas sur une copie dans ~/bin : un git pull
# met donc a jour le comportement, et deux copies ne peuvent pas diverger.
chmod +x "$REPO_DIR/ops/mini-stack-guard.sh" "$REPO_DIR/ops/mini-backup.sh"
CRON_TMP="$(mktemp)"
crontab -l 2>/dev/null | grep -v -e mini-stack-guard -e mini-backup > "$CRON_TMP" || true
printf '*/5 * * * * %s/ops/mini-stack-guard.sh\n' "$REPO_DIR" >> "$CRON_TMP"
printf '2-59/5 * * * * %s/ops/mini-load-marks.sh\n' "$REPO_DIR" >> "$CRON_TMP"
# La sauvegarde passe par ssh localhost : voir ops/mini-backup-launch.sh
printf '5 * * * * %s/ops/mini-backup-launch.sh --bronze-only\n' "$REPO_DIR" >> "$CRON_TMP"
printf '30 2 * * * %s/ops/mini-backup-launch.sh\n' "$REPO_DIR" >> "$CRON_TMP"
crontab "$CRON_TMP"
rm -f "$CRON_TMP"
echo "  crontab :"; crontab -l | sed 's/^/    /'
echo "  test garde-fou : colima stop ; sleep 300 ; colima status"
echo "  test sauvegarde : $REPO_DIR/ops/mini-backup.sh"

step "8. Sauvegarde externe (si le disque est monte)"
if [ -d /Volumes/Element1_APFS ]; then
  if [ ! -f /Volumes/Element1_APFS/.vse-backup-marker ]; then
    UUID="$(diskutil info /Volumes/Element1_APFS 2>/dev/null | awk -F': *' '/Volume UUID/{print $2}' | head -1)"
    printf 'VSE_BACKUP_1\nvolume_uuid: %s\ncreated_utc: %s\npurpose: sauvegardes vol-surface-engine (pg_dump + bronze Deribit)\n' \
      "$UUID" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > /Volumes/Element1_APFS/.vse-backup-marker
    echo "  marqueur cree (uuid $UUID)"
  fi
  "$REPO_DIR/ops/mini-backup.sh" || echo "  ATTENTION: la sauvegarde a echoue (voir ~/backup.log)"
else
  echo "  disque externe non monte : brancher /Volumes/Element1_APFS puis relancer"
  echo "    $REPO_DIR/ops/mini-backup.sh"
fi

step "Termine"
cat <<EOF
  Logs en direct      : cd $REPO_DIR && docker compose logs -f collector
  Etat                : docker compose ps collector ; cat data/deribit/health.json
  Rapport de qualite  : .venv/bin/python -m python.collect.analyze_bronze data/deribit
  Base de donnees     : docker compose ps db ; docker compose exec db psql -U vse -d volsurface
  Mise a jour du code : git pull && docker compose build collector && docker compose up -d collector
  Arret propre        : docker compose stop collector   (jamais 'down -v')
EOF
