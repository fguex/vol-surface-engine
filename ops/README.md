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

## Piège : modifier le collecteur exige un rebuild

  Le service `collector` exécute le code **embarqué dans l'image** (le montage `./python` est sur
  `ingest` seulement). Après une modification de `python/collect/` ou de la commande dans
  `compose.yaml`, un simple `docker compose up -d collector` recrée le conteneur avec l'ancien
  code : si la commande a gagné une option, le conteneur part en boucle de redémarrage sur
  `unrecognized arguments` (vécu). Il faut :

```
docker compose build collector && docker compose up -d collector
```

  Vérifier ensuite trois choses : `docker compose ps` (healthy), `data/deribit/health.json`
  (`subscribed: true`, `n_ticker` attendu) et la ligne `connected` du dernier événement, qui
  porte `n_channels`, `n_ticker` et les spots utilisés pour la bande ATM.

## Canaux souscrits

  `quote.*` sur tout l'univers (meilleur bid/ask), plus, par devise : `trades.option.*.100ms`,
  `deribit_price_index`, `markprice.options` (mark_iv et mark_price par strike, toute la chaîne),
  `deribit_volatility_index` (DVOL), `estimated_expiration_price`, `deribit_price_ranking`,
  `ticker.{DEVISE}-PERPETUAL.100ms` (funding, open interest, base), `perpetual.*`, les trades du
  perpétuel et `block_trades`. Et `ticker.{instrument}.100ms` sur une bande ATM
  (`--ticker-subset N` instruments par devise, recalculée à chaque session) : c'est le seul canal
  qui porte les greeks, l'open interest et bid/ask IV.

## Tâches planifiées (cron)

Deux entrées, toutes deux pointant sur le **repo** (pas sur une copie dans `~/bin`) :
un `git pull` met donc à jour le comportement, et deux copies ne peuvent pas diverger.

```
*/5 * * * *  ~/vol-surface-engine/ops/mini-stack-guard.sh              # remet Colima + la stack en route
 5  * * * *  ~/vol-surface-engine/ops/mini-backup.sh --bronze-only     # bronze -> disque externe (horaire)
30  2 * * *  ~/vol-surface-engine/ops/mini-backup.sh                   # bronze + dump de la base (quotidien)
```

Deux rythmes, parce que les deux objets n'ont pas la même nature : le bronze grandit en continu
(quelques Mo par heure, irremplaçable, il part toutes les heures à `:05`, cinq minutes après la
rotation du fichier en cours) ; le dump de la base est petit et c'est la référence restaurable,
le dupliquer 24 fois par jour n'apporterait rien. `~/backup_status.json` garde les deux
fraîcheurs séparément (`last_bronze_*` et `last_full_dump_*`).

Rien à voir dans la sortie : les deux scripts sont silencieux quand tout va bien. Leurs
traces sont dans `~/stack-guard.log` et `~/backup.log`.

## Sauvegarde vers le disque externe

Disque : WD 5 To, partition APFS `Element1_APFS` (3,5 To libres), dossier `Quant DB/`.
Le marqueur `/Volumes/Element1_APFS/.vse-backup-marker` porte l'UUID du volume.

Contenu de `Quant DB/` :

```
db/       volsurface-YYYY-MM-DD.dump   pg_dump -Fc, vérifié par pg_restore --list, 14 jours gardés
bronze/   copie du bronze Deribit     rsync incrémental, les *.part en cours sont ignorés
logs/     sorties de pg_dump et rsync, pour diagnostiquer sans deviner
```

### Les garde-fous, et le piège qu'ils couvrent

Si le disque n'est pas monté, `/Volumes/Element1_APFS` existe quand même : c'est un dossier
vide sur le disque **interne**. `rsync` y écrirait « avec succès » et on croirait avoir une
sauvegarde. Le script refuse de tourner si :

1. le chemin n'est pas un point de montage réel (`df` ne renvoie pas `/Volumes/Element1_APFS`) ;
2. le marqueur est absent ;
3. l'UUID du volume monté ne correspond pas à celui du marqueur (protège contre un autre
   disque branché au même point de montage — même modèle, même nom) ;
4. il reste moins de 20 Go libres, ou moins de 1,2 × la taille à copier.

En cas d'échec il écrit `~/backup_status.json` avec `ok: false` et la raison, et sort en
code 1. Un disque débranché est donc un cas normal et visible, pas un plantage silencieux.

### Restaurer — un backup jamais restauré n'est pas un backup

`ops/restore-dump.sh` restaure un dump dans une base jetable et **compare avec la production** :
c'est le test de sauvegarde, rejouable à volonté.

```bash
~/vol-surface-engine/ops/restore-dump.sh "/Volumes/Element1_APFS/Quant DB/db/volsurface-2026-10-13.dump"
```

Il refuse de viser `volsurface`, supprime la base de test à la fin (`--keep` pour la garder),
et sort 0 seulement si la restauration n'a **aucune** erreur et que les comptes sont identiques.

Trois choses mesurées le 13 oct. sur TimescaleDB 2.30, qui ne se devinent pas :

1. L'extension `timescaledb` doit exister **dans la base cible avant** `timescaledb_pre_restore()`.
   Sinon la fonction n'existe pas — elle est fournie par l'extension, pas par le serveur, et
   l'erreur (`function does not exist`) ne dit pas pourquoi.
2. Avec la bonne séquence : **0 erreur, 0 avertissement**, hypertables, index et clés
   étrangères restaurés à l'identique.
3. Sans elle : 2 erreurs (contrainte sur un chunk interne, `ONLY` sur une hypertable) et la
   clé étrangère `quote_curated_snapshot_id_fkey` manquante après restauration. Les données
   restent complètes, la clé se recrée d'un `ALTER TABLE ... ADD CONSTRAINT`, mais autant
   faire les choses dans le bon ordre.

Séquence exacte, si tu préfères à la main :

```bash
docker compose exec -T db psql -U vse -d volsurface -c 'CREATE DATABASE restore_test'
docker compose exec -T db psql -U vse -d restore_test -c 'CREATE EXTENSION IF NOT EXISTS timescaledb'
docker compose exec -T db psql -U vse -d restore_test -tAc 'SELECT timescaledb_pre_restore()'
docker compose exec -T db pg_restore -U vse -d restore_test --no-owner < DUMP
docker compose exec -T db psql -U vse -d restore_test -tAc 'SELECT timescaledb_post_restore()'
```

### Sauvegarder hors machine (3-2-1)

Le disque externe reste branché au mini : un vol, un incendie ou une panne d'alimentation
emporte les deux copies. La troisième copie se fait depuis le MacBook, qui tire les dumps et
le bronze par SSH (`rsync`), à cadence hebdomadaire. À mettre en place quand le MacBook est
joignable de façon fiable.


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
