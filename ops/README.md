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

## Les couches : bronze / silver / gold

Le schema ne dit plus seulement « une base » mais **quelle** couche : `silver` porte les
tables de travail, `gold` les vues de lecture, `bronze` est un repere (les donnees brutes
sont des fichiers, pas des tables). Voir `sql/008_couches.sql`.

    silver   instrument (SCD2), option_mark, perp_tick, vol_index     <- ecrit par load_marks
    gold     v_spot, v_surface, v_atm_terme, v_perp_funding           <- lu par les clients
    public   schema_migrations                                        <- le registre du migrateur

Le role recoit `search_path = silver, gold, public` au niveau de la **base**, donc les
requetes a la main et les scripts d'exploitation continuent de fonctionner sans prefixe
(`ops/db-tour.sh` en depend, volontairement, et le dit).

**Piege operationnel : `pg_dump` ne transporte pas ce reglage.** Les `ALTER DATABASE ... SET`
ne font pas partie d'un dump : une base restauree n'a donc PAS ce search_path, et une
requete non qualifiee y echoue. C'est pourquoi `ops/restore-dump.sh` qualifie ses noms
(`silver.option_mark`, `public.schema_migrations`) — sans quoi la verification de sauvegarde
casserait précisément le jour ou l'on en a besoin.

## Piège : modifier le collecteur exige un rebuild

  Le service `collector` exécute le code **embarqué dans l'image** (aucun montage de `./python`).
  Après une modification de `python/collect/` ou de la commande dans
  `compose.yaml`, un simple `docker compose up -d collector` recrée le conteneur avec l'ancien
  code : si la commande a gagné une option, le conteneur part en boucle de redémarrage sur
  `unrecognized arguments` (vécu). Il faut :

```
docker compose build collector && docker compose up -d collector
```

  Vérifier ensuite trois choses : `docker compose ps` (healthy), `data/deribit/health.json`
  (`subscribed: true`, `n_ticker` attendu) et la ligne `connected` du dernier événement, qui
  porte `n_channels`, `n_ticker` et les spots utilisés pour la bande ATM.

## Bronze -> base : le chargement des marques

  Le bronze est brut et volumineux ; la base ne doit contenir que ce qu'on interroge. Le job
  `python.jobs.load_marks` (lance par `mini-load-marks.sh`) echantillonne donc le bronze en
  tranches de 5 minutes :

    option_mark   mark_iv et mark_price par strike (toute la chaine), plus bid/ask IV, greeks,
                  open interest et prix sous-jacent pour la bande ATM
    perp_tick     index, funding courant et sur 8 h, taux, open interest, meilleur bid/ask
    vol_index     DVOL par devise

  Deux proprietes a ne jamais casser :

    - idempotent : la cle est (instrument_sk, observed_at), l'insertion se fait en
      ON CONFLICT DO NOTHING. Rejouer une plage ne duplique rien. C'est ce qui rend un
      rattrapage, ou une restauration, sans danger.
    - deterministe : `observed_at` vient de l'horodatage du fournisseur, jamais de l'heure
      d'execution. Rejouer le meme bronze donne les memes lignes.

  Verifier : `psql -c "select count(*), min(observed_at), max(observed_at) from option_mark"`
  et `tail -20 ~/load-marks.log`. Pour un rattrapage : `--all` (tout le bronze disponible).

### Fraicheur : ce qui est live et ce qui ne l'est pas

  Deux maillons, deux rythmes — a ne pas confondre :

    Deribit -> bronze   PUSH. Le collecteur est ABONNE (websocket) : chaque tick arrive et est
                        ecrit dans la seconde. Aucun sondage. Un rafraichissement des
                        instruments toutes les 15 min (`instrument_refresh`) rattrape les
                        nouvelles echeances ; le watchdog coupe et reabonne si rien n'arrive.
    bronze -> base      SONDAGE. Le cron passe toutes les 5 minutes, et relit le bronze.

  Le fichier de l'heure en cours (`.part`) est lu lui aussi : le lecteur s'arrete a la derniere
  trame complete, et sa derniere tranche de 5 min — incomplete — est ecartee, parce qu'une ligne
  deja en base n'est jamais reecrite. Sans cette regle, une tranche serait figee sur ses
  premieres secondes.

  Mesure du retard de la base (14 octobre, avant/apres ce changement) :

    avant   max(observed_at) = 07:00, now = 07:52   ->  52 min   (le fichier de l'heure etait
                                                                  ignore jusqu'a sa fermeture)
    apres   max(observed_at) = 07:45, now = 07:52   ->   7 min   (derniere tranche complete)

  Invariant a surveiller, il tient en une requete :

```sql
SELECT max(observed_at) < date_trunc('hour', now())
                        + floor(extract(minute from now())/5) * interval '5 min' AS ok
  FROM option_mark;
```

  Le retard est donc borne par (largeur d'une tranche + cadence du cron), soit ~10 minutes. Pour
  descendre plus bas, ce n'est pas le code qu'il faut changer mais la ligne de cron :
  `* * * * *` au lieu de `2-59/5`. En dessous de 5 minutes, seuls les premiers instants d'une
  tranche se gagnent — le grain de la base reste la tranche.

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
*/5 * * * *    ~/vol-surface-engine/ops/mini-stack-guard.sh                # remet Colima + la stack en route
*/10 * * * *   ~/vol-surface-engine/ops/mini-alerte.sh                     # veille : sauvegarde, collecte, base, disques
2-59/5 * * * * ~/vol-surface-engine/ops/mini-load-marks.sh                 # bronze -> base (tranches de 5 min)
 5  * * * *    ~/vol-surface-engine/ops/mini-backup-launch.sh --bronze-only  # bronze -> disque externe (horaire)
30  2 * * *    ~/vol-surface-engine/ops/mini-backup-launch.sh              # bronze + dump de la base (quotidien)
 0  4 * * 0    ~/vol-surface-engine/ops/mini-verif-restauration.sh         # restauration de controle (dimanche)
```

**Pourquoi la sauvegarde passe par `mini-backup-launch.sh`** (et pas directement par
`mini-backup.sh`) : un processus lance par cron n'a pas l'autorisation macOS d'ecrire sur un
volume amovible, et ce mini n'a pas de session graphique pour la lui accorder. Le resultat,
vecu : un rsync qui echoue en une seconde, cinq heures de suite, avec un message d'erreur
ecrit... sur le volume injoignable. Le lanceur passe par `ssh localhost`, dont le processus
a l'autorisation. Alternative plus propre si un jour l'acces est accorde a cron :
`Reglages > Confidentialite et securite > Acces complet au disque`, ajouter `/usr/sbin/cron`.

`mini-backup.sh` verifie desormais l'ECRITURE avant de commencer : volume monte et marqueur
present ne suffisent pas, un volume peut etre en lecture seule ou inaccessible.

`mini-load-marks.sh` echantillonne le bronze vers la base (voir plus bas) ; il tourne a `:02, :07, ...`
pour ne pas tomber en meme temps que la copie du bronze a `:05`. Chaque passage ecrit dans
`~/load-marks.log`.

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
   clé étrangère de la table de faits manquante après restauration (vécu sur les hypertables
   CBOE, depuis retirées ; le mécanisme est le même pour `option_mark`). Les données
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


## Veille et alertes

Rien ne lisait les journaux. Le 14 oct. 2026, la sauvegarde quotidienne a échoué dès la
première étape (aucun dump écrit) et l'information est restée dans `~/backup.log`, que
personne ne consulte. Un échec silencieux n'est pas une sauvegarde ratée : c'est une
sauvegarde qu'on **croit** avoir.

`ops/mini-alerte.sh` (cron, toutes les 10 min) contrôle six choses et ne parle que sur
changement d'état :

    sauvegarde        ~/backup_status.json ok, bronze < 2 h, dump complet < 36 h
    collecte          data/deribit/health.json : dernière trame < 180 s
    base              au moins une ligne chargée dans les 20 dernières minutes
    restauration      la passe hebdomadaire a réussi, et date de moins de 8 jours
    disque            > 10 Go libres en interne
    disque_externe    monté, et > 50 Go libres

Il tourne sur l'**hôte**, pas dans un conteneur : une des pannes à détecter est l'arrêt de
Colima, et un veilleur hébergé dans ce qu'il surveille ne peut pas signaler sa propre mort.
Il prévient à l'apparition, insiste toutes les 6 h tant que ça dure, et signale le
rétablissement — sans quoi un problème permanent enverrait 144 messages par jour et plus
personne ne les lirait.

Installation :

```bash
python3 -c "import secrets; print('vse-' + secrets.token_hex(9))"     # le topic
echo 'NTFY_TOPIC=<ce-topic>' >> ~/vol-surface-engine/.env && chmod 600 ~/vol-surface-engine/.env
chmod +x ~/vol-surface-engine/ops/mini-alerte.sh ~/vol-surface-engine/ops/mini-verif-restauration.sh
( crontab -l 2>/dev/null; \
  echo "*/10 * * * * $HOME/vol-surface-engine/ops/mini-alerte.sh"; \
  echo "0 4 * * 0 $HOME/vol-surface-engine/ops/mini-verif-restauration.sh" ) | crontab -
```

Vérification, dans cet ordre :

```bash
ops/mini-alerte.sh               # "tout va bien", et n'envoie rien
ops/mini-alerte.sh --test        # fait vibrer le téléphone (teste le canal, pas les contrôles)
ops/mini-verif-restauration.sh   # restaure le dernier dump pour de vrai
cat ~/verif_restauration_status.json
```

### Restauration de contrôle

`ops/mini-verif-restauration.sh` prend le dump le plus récent du disque externe, appelle
`ops/restore-dump.sh` (qui restaure dans une base jetable et compare les comptes avec la
production), puis écrit `~/verif_restauration_status.json` — que la veille lit. Comme la
sauvegarde, il passe par `ssh localhost` : un processus lancé par cron n'a pas l'autorisation
macOS de lire le disque amovible.

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
