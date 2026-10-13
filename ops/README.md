# ops/ — exploitation du Mac mini

## Migrations de schéma

Le schéma ne vient plus de `docker-entrypoint-initdb.d` (qui ne s'exécute qu'à la
**première** initialisation du volume, donc jamais sur une base existante, et sans
laisser de trace de ce qui a été appliqué). Les migrations sont la seule source de
vérité :

```bash
cd ~/vol-surface-engine
set -a; . ./.env; set +a
export PGHOST=127.0.0.1 PGUSER=vse PGDATABASE=volsurface PGPASSWORD="$POSTGRES_PASSWORD" PYTHONPATH=.
.venv/bin/python -m python.db.migrate --status     # où en est-on ?
.venv/bin/python -m python.db.migrate --dry-run    # que ferait-il ?
.venv/bin/python -m python.db.migrate              # applique ce qui manque
```

Règles, appliquées par le runner :

- fichiers `sql/NNN_nom.sql`, numéro croissant, jamais réutilisé ;
- une migration déjà appliquée ne se **modifie jamais** : le sha256 est enregistré et
  toute retouche fait échouer le runner (exit 3) **avant** d'appliquer quoi que ce soit.
  Pour corriger, on crée la migration suivante ;
- chaque fichier tourne dans une transaction, sauf s'il déclare
  `-- migrate:no-transaction` en tête (nécessaire pour `CREATE INDEX CONCURRENTLY`) ;
- verrou consultatif : deux exécutions simultanées sont impossibles ;
- rejouer le runner sur une base à jour ne fait rien.

Base créée avant l'introduction des migrations (cas du Mac mini) : on enregistre les
migrations déjà appliquées à la main, sans les exécuter.

```bash
.venv/bin/python -m python.db.migrate --baseline-through 001
```

Codes de sortie : `0` ok, `1` erreur, `2` usage, `3` migration modifiée après application.

## Redémarrage automatique de la stack (Colima → Docker → conteneurs)

Le problème, mesuré le 11 oct. 2026 : après un reboot, Colima ne revient pas tout seul.
Conséquence chiffrée : **9 h de ticks Deribit perdus** (09:26 → 18:24 UTC).

Pourquoi les solutions « évidentes » ne marchent pas ici :

- `brew services start colima` installe un **LaunchAgent**, qui ne s'exécute que dans une
  session graphique ouverte. Sur un Mac mini headless, il n'y a souvent personne connecté.
- Lancé depuis une session SSH, `brew services` échoue de toute façon :
  `launchctl enable gui/<uid>/sh.brew.colima` → erreur **125** (le domaine graphique n'est
  pas pilotable à distance).

### Solution retenue : `mini-stack-guard.sh` + cron (aucun sudo)

`cron` tourne comme daemon système, indépendamment de toute session graphique — vérifié sur
la machine. Le script est idempotent : il ne fait rien quand tout va bien, et sinon il
redémarre Colima puis `docker compose up -d`, en moins de 5 minutes (mesuré : 91 s sur un
tick favorable).

Installation :

```bash
mkdir -p ~/bin
cp ~/vol-surface-engine/ops/mini-stack-guard.sh ~/bin/
chmod +x ~/bin/mini-stack-guard.sh
crontab -l 2>/dev/null | grep -v mini-stack-guard > /tmp/ct || true
echo "*/5 * * * * $HOME/bin/mini-stack-guard.sh" >> /tmp/ct
crontab /tmp/ct && rm /tmp/ct
crontab -l
```

Vérification (c'est le test qui compte : il coupe vraiment la stack, ~2 min de données) :

```bash
colima stop
sleep 300
colima status        # doit dire "colima is running"
tail -20 ~/stack-guard.log
```

`~/stack-guard.log` est borné (400 dernières lignes au-delà de 200 Ko) et ne contient que
les événements anormaux.

Le script est aussi installé automatiquement par `scripts/macmini_bootstrap.sh` (étape 7).

### Alternative : LaunchDaemon système (`com.local.colima.plist`)

Utile seulement si quelqu'un a le mot de passe admin et préfère un démarrage immédiat au
boot plutôt qu'un rattrapage en ≤ 5 min. Commandes dans l'en-tête du fichier. Ne pas faire
les deux à la fois : le garde-fou cron est un no-op si Colima tourne déjà, mais autant
éviter deux mécanismes concurrents.
