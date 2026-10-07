# Plan — DB + ingestion automatique + monitoring sur le Mac mini

## TLDR (explain like I'm 5)

- Le Mac mini devient une petite "usine" qui tourne seule.
- La base de données = un classeur. Le container Docker = le meuble qui contient le classeur.
  Le volume Docker = le tiroir : on peut jeter et remplacer le meuble, le tiroir garde les feuilles.
- Un réveil (scheduler) lance toutes les heures, pendant que la bourse US est ouverte,
  un ouvrier (le job d'ingestion) qui va chercher les prix des options et les range dans le classeur.
- Un gardien (monitoring) vérifie chaque jour que l'ouvrier est bien passé, sinon il t'envoie une alerte.
- Une photocopie du classeur (backup) est faite chaque nuit, parce que le tiroir peut aussi casser.

Ce qui existe déjà dans le repo : compose.yaml (db + ingest), Dockerfile, sql/001_schema.sql,
python/jobs/ingest.py avec idempotence et table job_run. Il manque : le runtime Docker headless
sur le Mac mini, le scheduler, le monitoring, les backups, et une stratégie de migrations.

---

## Phase 0 — Préparer le Mac mini (une fois)

Objectif : une machine qui ne dort pas, accessible en SSH, avec Docker qui démarre tout seul.

1. SSH (depuis l'écran du Mac mini ou Partage d'écran)
   - Réglages Système → Général → Partage → Session à distance : ON
   - Depuis le MacBook : `ssh felixguex@<nom>.local`
   - Clé SSH : `ssh-keygen -t ed25519` sur le MacBook puis `ssh-copy-id felixguex@<nom>.local`

2. Ne jamais dormir, redémarrer après coupure
   - `sudo pmset -a sleep 0 disksleep 0 autorestart 1 womp 1`
   - Vérifier : `pmset -g`

3. Outils
   - Homebrew : https://brew.sh
   - `brew install git colima docker docker-compose`
   - Pourquoi Colima plutôt que Docker Desktop : Docker Desktop a besoin d'une session
     graphique ouverte ; sur un Mac headless il ne redémarre pas proprement après un reboot.
     Colima tourne comme service brew (launchd), sans GUI.
   - `colima start --cpu 4 --memory 6 --disk 60 --vm-type vz`
   - `brew services start colima`  → redémarre au boot
   - Test : `docker run --rm hello-world`
   - Alternative acceptable : OrbStack (plus confortable, mais app GUI / licence).

4. Code
   - `git clone https://github.com/fguex/vol-surface-engine.git ~/vol-surface-engine`
   - Le Mac mini ne fait que `git pull` : on ne développe pas dessus.

Critère de sortie : après un `sudo reboot`, `ssh` marche et `docker ps` répond sans rien toucher.

---

## Phase 1 — La base de données (Timescale dans Docker, données dans un volume)

Objectif : une DB qui survit aux redémarrages et aux reconstructions de containers.

Ce qui est déjà bon dans compose.yaml
- volume nommé `pgdata` → persistance
- `./sql` monté dans `/docker-entrypoint-initdb.d` → schéma appliqué automatiquement
- healthcheck `pg_isready`

À corriger avant la prod
1. Épingler l'image : `timescale/timescaledb:2.x.y-pg16` (pas `latest`). Une mise à jour
   majeure silencieuse de Postgres rendrait le volume illisible.
2. `restart: unless-stopped` sur `db`.
3. Port : `"127.0.0.1:5432:5432"` au lieu de `"5432:5432"`.
   Accès depuis le MacBook via tunnel SSH : `ssh -L 5432:localhost:5432 felixguex@<nom>.local`
   → la DB n'est jamais exposée au réseau, même local.
4. `.env.example` est dans .gitignore → l'enlever de .gitignore et le committer
   (sans vrai mot de passe). Seul `.env` reste ignoré.
5. Séparer dev et prod :
   - `compose.yaml` = base commune (sans le bind mount `./python`)
   - `compose.override.yaml` = dev (hot-reload `./python`), chargé automatiquement en local
   - `compose.prod.yaml` = prod (restart, logging), lancé avec `-f compose.yaml -f compose.prod.yaml`

Piège à connaître : les scripts de `/docker-entrypoint-initdb.d` ne s'exécutent QUE si le volume
est vide. Toute modification future du schéma ne sera pas appliquée automatiquement.
→ Phase 1b : migrations.

Phase 1b — Migrations minimales
- Fichiers numérotés `sql/001_schema.sql`, `sql/002_....sql`, …
- Table `schema_migrations(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ)`
- Petit script `python/db/migrate.py` : applique dans l'ordre les fichiers non encore appliqués,
  chacun dans une transaction. Lancé avant chaque déploiement.
- Pas besoin d'Alembic/Flyway à ce stade.

Commandes
- `docker compose up -d db`
- `docker compose ps` → `healthy`
- `docker compose exec db psql -U vse -d volsurface -c '\dt'`
- `docker volume inspect vol-surface-engine_pgdata`

Critère de sortie : `docker compose down` puis `up -d` → les tables et les lignes sont toujours là.
(`down -v` détruit le volume : ne jamais le taper en prod.)

---

## Phase 2 — Ingestion manuelle validée

Objectif : un run à la main qui écrit en base, rejouable sans doublon.

1. `docker compose build ingest`
2. `docker compose run --rm ingest python -m python.jobs.ingest --tickers SPX`
3. Vérifier
   - `SELECT snapshot_id, underlying, capture_slot, n_rows_raw FROM snapshot ORDER BY capture_slot DESC LIMIT 5;`
   - `SELECT count(*) FROM quote_curated;`
   - `SELECT * FROM job_run ORDER BY started_at DESC LIMIT 5;`
4. Test d'idempotence : relancer dans la même heure → log "snapshot … déjà présent", 0 nouvelle ligne.
5. Test d'échec : couper la DB (`docker compose stop db`), lancer → exit code ≠ 0, log clair.

Point à vérifier dans le code : quand la DB est down, l'écriture de `job_run` échoue aussi
(elle vit dans la même DB) → l'échec n'est visible que dans les logs. C'est pour ça que le
monitoring de la Phase 4 doit vérifier la *fraîcheur* (absence de succès récent), pas seulement
la présence de lignes "error".

Critère de sortie : 3 tickers ingérés, relance idempotente, échec visible.

---

## Phase 3 — Planification (le "réveil")

Recommandation : un service `scheduler` dans compose, avec supercronic, plutôt que cron/launchd sur l'hôte.

Pourquoi
- Tout est décrit dans le repo (reproductible, versionné).
- `TZ=America/New_York` dans le container → les horaires suivent le marché US
  y compris les changements d'heure US/EU qui ne tombent pas le même week-end.
  Avec launchd sur l'hôte, en heure de Zurich, tu serais décalé d'une heure 2 à 3 semaines par an.
- Logs dans `docker compose logs scheduler`.

Planning proposé (ET)
- Toutes les heures de 10:00 à 16:00, lundi–vendredi : `0 10-16 * * 1-5`
  (capture_slot = 60 min dans ingest.py → cohérent ; évite les 30 premières minutes bruitées)
- Un snapshot de clôture 16:05 si tu veux une surface "EOD" de référence.
- Jours fériés US : le job tournera ; le contrôle d'admissibilité / l'idempotence doivent
  absorber ça (à vérifier : que fait check_admissible un jour férié ?).

Forme
- Image = la même que `ingest` + binaire supercronic.
- `crontab` versionné dans `ops/crontab`.
- Le scheduler lance `python -m python.jobs.ingest` directement (pas de docker-in-docker).
- `restart: unless-stopped`.

Alternative plus simple si tu veux aller vite : launchd sur l'hôte qui exécute
`docker compose run --rm ingest`. Acceptable, mais moins propre (config hors repo, problème d'heure).

Critère de sortie : sur une journée de marché, 7 snapshots par ticker, sans intervention.

---

## Phase 4 — Monitoring

Objectif : savoir en < 1 jour qu'il y a un problème, sans aller regarder.

Niveau 1 (indispensable)
1. Vues SQL (`sql/00x_monitoring_views.sql`)
   - `v_last_snapshot` : dernier capture_slot et n_rows par underlying
   - `v_job_health` : derniers job_run, taux d'erreur sur 7 jours
   - `v_daily_counts` : nombre de snapshots par jour et par underlying
2. Job `python/jobs/healthcheck.py` (planifié par le même scheduler, ex. 16:30 ET)
   - ÉCHEC si : aucun snapshot réussi depuis > 2 h pendant les heures de marché,
     nombre de snapshots du jour < attendu, chute brutale de n_rows (> 50 %),
     dernier job_run en status = error
   - Alerte via ntfy.sh (une requête HTTP POST, notification sur le téléphone) ou email.
3. Heartbeat externe (gardien du gardien) : healthchecks.io gratuit.
   Le healthcheck ping une URL à chaque succès ; si le ping ne vient pas, healthchecks.io t'alerte.
   Couvre le cas "Mac mini éteint / Docker mort", que rien d'interne ne peut détecter.

Niveau 2 (plus tard, utile pour la démo à Sylvestre)
- Grafana dans compose, branché sur Timescale (port 127.0.0.1:3000, via tunnel SSH).
- Panels : snapshots/jour, rows par snapshot, latence des jobs, et plus tard
  RMSE et eta_ratio des calibrations dans le temps.

Système
- `docker system df` et espace disque : les parquet bruts grossissent chaque heure.
- Logs Docker : limiter (`logging: driver: local, options: max-size: 10m`).

---

## Phase 5 — Backups et rétention

- Le volume n'est pas un backup : un disque mort ou un `down -v` et tout est perdu.
- Nightly : `pg_dump -Fc` vers `~/backups/volsurface/YYYY-MM-DD.dump` (job du scheduler ou launchd),
  garder 14 jours.
- Copie hors machine : disque externe ou le MacBook (rsync) — au minimum hebdo.
- Test de restauration une fois : `pg_restore` dans une DB vide. Un backup jamais restauré n'est pas un backup.
- Données brutes `data/raw` (parquet) : c'est la vraie source de vérité, à sauvegarder aussi.
- Timescale plus tard : compression des chunks de `quote_curated` > 7 jours.

---

## Phase 6 — Brancher la calibration (prépare la suite)

- Job `calibrate` : lit le dernier snapshot depuis la DB (ou le CSV exporté), lance le runner C++,
  lit le JSON de diagnostics, écrit `calibration_run`, `calibration_slice`, `quote_fit`.
- Le C++ reste sans dépendance DB : Python orchestre, C++ calcule.
- Il faudra un 2e Dockerfile (ou multi-stage) qui compile le C++ (Eigen, NLopt) pour arm64.

---

## Mode haute fréquence (1 min) — décision d'architecture

Mesures (7 oct. 2026, marché fermé) sur https://cdn-api.cboe.com/api/global/delayed_quotes/options/_SPX.json :
- 30 634 options, 13,6 MB JSON, 1,8 MB gzip, 1,0–1,6 s par requête, cache CDN s-maxage=5
- Parquet brut existant : ~1,3 MB / snapshot ; chaîne curée SPX : ~10 900 lignes
- À VÉRIFIER pendant la séance : le contenu change-t-il vraiment chaque minute ? (hash du payload)

Volumes estimés pour SPX seul, 252 jours/an :
- 1 min (390/jour)  : brut ~0,5 GB/jour (~130 GB/an) ; curé ~4,3 M lignes/jour, ~30 GB/an une fois compressé par Timescale
- 5 min (78/jour)   : brut ~100 MB/jour (~25 GB/an) ; curé ~5 GB/an compressé

Changements nécessaires par rapport au mode horaire :
1. Collecteur long-running (boucle alignée sur la minute), pas cron + nouveau container :
   l'import d'openbb à lui seul prend plusieurs secondes.
2. Fetch direct du JSON CBOE (httpx + gzip) au lieu d'openbb dans la boucle.
3. capture_slot(minutes=1) ; snapshot_id inchangé → idempotence conservée.
4. Hash du payload : si identique au précédent → snapshot non écrit, compteur "stale".
5. Timescale : compression des chunks de plus d'un jour, rétention du brut parquet 30 jours.
6. Calibration = consommateur séparé, en mode "latest wins" : il calibre toujours le dernier
   snapshot disponible et saute ceux qu'il n'a pas eu le temps de traiter. Le temps de fit est la métrique clé.
7. Monitoring : âge du dernier snapshot (alerte si > 3 min en séance), latence du fetch,
   ratio de snapshots stale, retard de la calibration.

Limite honnête : les données ont 15 min de retard. 1 min, c'est la bonne fréquence
d'échantillonnage pour s'entraîner au workflow, pas une fréquence de trading.

## Ordre de travail recommandé

1. Phase 0 (Mac mini prêt, reboot-proof)                    ~1 h
2. Phase 1 + correctifs compose                              ~1 h
3. Phase 2 (ingestion manuelle validée)                      ~1 h
4. Phase 3 (scheduler) → laisser tourner 1 journée
5. Phase 4 niveau 1 + Phase 5 backup nightly
6. Phase 1b migrations dès le premier changement de schéma
7. Phase 4 niveau 2 + Phase 6

## Questions à trancher

- Source des données : CBOE delayed est-il assez fiable / autorisé pour un usage horaire ?
- Fréquence : horaire suffit-il, ou seulement 2-3 snapshots/jour (ouverture, mid, clôture) ?
- Univers : SPX seul au début, ou SPX + SPY + AAPL dès maintenant ?
- Rétention : combien de temps garder les parquet bruts ?
