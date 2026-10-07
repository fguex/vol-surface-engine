# Architecture des données — médaillon + reproductibilité des fits

## TLDR (explain like I'm 5)

- Bronze = la photo originale. On ne la retouche jamais.
- Silver = la photo rangée : chaque option est une ligne propre, avec des types corrects et un motif de rejet au lieu d'une suppression.
- Gold = la photo préparée pour le fit : forward, log-moneyness k, vol implicite, poids.
- Fit = ce qu'on en tire : paramètres SSVI, diagnostics et la recette exacte qui a servi.
- Pour refaire un fit à l'identique, il faut : la même photo (bronze, immuable), la même recette (version du code et de la config), et les mêmes ingrédients externes (taux, dividendes, calendrier) tels qu'on les connaissait à ce moment-là.
- "valid_from / valid_to" ne sert pas pour les photos, qui sont des instants. Il sert pour les ingrédients externes qui changent ou sont corrigés.

---

## 0. Statut juridique de la source (vérifié le 7 oct. 2026)

Source : Cboe "Terms and Conditions for Use of Cboe Websites", https://www.cboe.com/terms (Last Updated: November 16, 2022).

Texte clé :
> "You may view, print and download one copy of the Materials for your personal non-commercial use [...]. You may not otherwise copy, reproduce, alter, store either in hard copy or in an electronic retrieval system, [...] create a derivative work [...] without Cboe's prior written consent [...]. To formally request such consent you must submit a Request to Use Cboe Content."

Lecture (je ne suis pas juriste) :
- `cdn-api.cboe.com/.../delayed_quotes/...` n'est pas une API publique documentée : c'est le backend du site, donc ces conditions s'appliquent.
- Archiver chaque minute dans une base de données, c'est "store in an electronic retrieval system". Les conditions ne l'autorisent pas sans accord écrit, même pour un usage personnel.
- OpenBB (provider="cboe") appelle le même endpoint. Ça ne change rien à la licence.
- robots.txt de www.cboe.com ne bloque pas ce chemin, mais robots.txt ne vaut pas licence.
- Montrer des résultats à une entreprise (Sylvestre) rapproche l'usage d'un usage commercial.

Options :
1. Demander l'accord écrit via "Request to Use Cboe Content" en décrivant un usage éducatif et personnel.
2. Passer à une source sous licence : Cboe DataShop, Databento (OPRA), Polygon/Massive, ThetaData, Tradier, Interactive Brokers. Prix et conditions à vérifier.
3. En attendant : petite fréquence, pas de redistribution, pas de démo de données brutes.

Conséquence d'architecture : le provider doit être un plugin. Le bronze stocke "provider + payload brut", et tout ce qui est en aval ne connaît que le schéma silver. Changer de source ne doit toucher que fetch + parsing silver.

---

## 1. Les couches

### Bronze — brut, immuable, append-only
- Contenu : le payload exact renvoyé par le provider (JSON compressé en zstd), sans aucun parsing.
- Stockage : fichiers, pas Postgres.
  `bronze/provider=cboe/underlying=SPX/date=2026-10-07/1430Z_<hash>.json.zst`
- Manifest en DB, table `bronze_snapshot` :
  - `snapshot_id` (déterministe : provider|underlying|slot)
  - `provider`, `underlying`, `capture_slot`
  - `fetched_at` : heure murale du fetch
  - `source_ts` : horodatage fourni par le provider, par ex. le champ `timestamp` de CBOE. C'est l'heure du marché que la donnée représente (environ 15 min de retard).
  - `payload_sha256`, `bytes`, `path`, `fetcher_version`, `http_status`, `latency_ms`
- Règles : on ne modifie ni ne supprime jamais un fichier bronze, sauf via la politique de rétention. Si le payload est identique au précédent (même sha), on enregistre la ligne du manifest avec `is_duplicate = true`, sans écrire de fichier.

### Silver — normalisé, typé, une ligne par option
- Fonction pure : `silver = parse(bronze, parser_version)`.
- Colonnes : `snapshot_id`, `source_ts`, `underlying`, `root`, `expiry`, `settlement` (AM/PM), `strike`, `is_call`, `bid`, `ask`, `bid_size`, `ask_size`, `volume`, `open_interest`, `last_trade_ts`, `underlying_bid/ask/spot`, `reject_reason`.
- On ne supprime pas : on marque avec `reject_reason` (spread négatif, bid=0, échéance passée, etc.).
- Stockage : Parquet partitionné par date. On le requête avec DuckDB, ou on le met dans une hypertable Timescale compressée si on veut du SQL.
- Volume SPX à 1 min : environ 30 000 lignes × 390 relevés ≈ 12 M lignes/jour. Parquet est nettement moins cher que Postgres à ce volume.

### Gold — prêt pour le fit
- Fonction pure : `gold = build(silver, market_inputs, recipe)`.
- `market_inputs` :
  - courbe de taux
  - dividendes / forward
  - calendrier
- `recipe` :
  - filtres (fenêtre de k, T minimum, spread maximum)
  - choix du forward (parité put-call ou modèle)
  - pondération
- Colonnes : `snapshot_id`, `recipe_id`, `expiry`, `t_years`, `strike`, `k`, `forward`, `df`, `mid`, `iv_market`, `vega`, `weight`, `included`.
- Stockage : en DB, mais seulement pour les snapshots réellement calibrés. Pour les autres, gold se recalcule (c'est déterministe).

### Fit — résultats
- `calibration_run` :
  - `run_id`, `snapshot_id`, `recipe_id`, `market_inputs_id`
  - `engine_git_sha`, `optimizer_config`
  - `params` (rho, eta, gamma, nu, ou par échéance)
  - `loss`, `eta_ratio`, `status`, `fit_ms`
  - `input_hash` : hash du dataset gold réellement passé au moteur
- `calibration_slice`, `quote_fit` : diagnostics, comme dans 001_schema.sql.

---

## 2. Reproductibilité : faut-il un valid_from / valid_to ?

Il faut distinguer trois sortes d'objets.

| Objet | Nature | Ce qu'il faut |
|---|---|---|
| Snapshot de marché (bronze / silver) | événement à un instant | `source_ts` + `fetched_at`, immuable. Pas de valid_from/to. |
| Recette, code, config | version | identifiant immuable : hash de la config + git sha. Pas de valid_from/to. |
| Données de référence (taux, dividendes, calendrier, specs de contrat) | état qui change ET qui peut être corrigé | bitemporel : `valid_from/valid_to` + `recorded_at` |

Pourquoi deux axes de temps pour les données de référence :
- `valid_from / valid_to` (temps métier) : la période où la valeur s'applique. Exemple : le dividende attendu pour le Q4.
- `recorded_from / recorded_to` (temps système) : la période pendant laquelle on croyait que c'était la bonne valeur. Exemple : une estimation de dividende corrigée le 12 octobre.
- Pour rejouer un fit fait le 10 octobre à 14h30 "comme à l'époque", on veut les valeurs valides à `source_ts` ET connues à `run_started_at`. Si on prend la valeur corrigée plus tard, on introduit un biais de look-ahead.

Requête type, "as of" :
```sql
SELECT * FROM dividend_forecast
WHERE underlying = 'SPX'
  AND valid_from  <= :source_ts AND (:source_ts  < valid_to    OR valid_to    IS NULL)
  AND recorded_from <= :run_ts  AND (:run_ts     < recorded_to OR recorded_to IS NULL);
```

Version plus simple, recommandée pour démarrer : versionnage immuable.
- Chaque courbe de taux ou jeu de dividendes construit reçoit un `market_inputs_id` (hash du contenu), et n'est jamais modifié.
- `calibration_run` pointe vers ce `market_inputs_id`.
- Rejouer = relire le snapshot, la recette et le market_inputs_id. Pas besoin de requêtes bitemporelles.
- Le bitemporel complet ne devient nécessaire que si on veut répondre à "qu'aurait-on calibré à 14h30 avec ce qu'on savait à 14h30 ?" pour des données de référence corrigées a posteriori. C'est la vraie question d'un backtest honnête, donc à garder pour la phase 2.

Règle d'or : un `calibration_run` est reproductible si et seulement si
`(snapshot_id, recipe_id, market_inputs_id, engine_git_sha, optimizer_config)` pointent tous vers des objets immuables, et que `input_hash` est le même quand on le recalcule.

---

## 3. Optimisations pour le mode 1 minute

1. Bronze sur disque en zstd. Postgres ne contient que le manifest.
2. Déduplication par hash du payload : si CBOE n'a pas rafraîchi, on n'écrit rien.
3. Silver en Parquet partitionné par jour (DuckDB). Compression Timescale si on passe en DB.
4. Gold matérialisé seulement pour les snapshots calibrés.
5. Calibration en mode "latest wins" : on calibre le dernier snapshot disponible et on saute ceux qui sont en retard.
6. Warm start : le fit à t part des paramètres du fit à t-1. C'est ce que fait un desk, et ça réduit fortement le temps de fit.
7. Rétention :
   - bronze 30 à 90 jours
   - silver complet 1 an, puis seulement un sous-échantillon (par ex. 1 relevé toutes les 15 min)
   - gold et fits gardés indéfiniment (petits)
8. Replay : un script qui rejoue une journée de bronze minute par minute dans le pipeline. C'est le test de performance réaliste.

---

## 4. Ordre d'implémentation

1. Trancher la question de la source / licence (section 0).
2. `bronze_snapshot` + writer bronze (payload brut + hash + source_ts).
3. Parser silver (fonction pure, testée sur 2 ou 3 payloads figés en fixtures).
4. `market_inputs` versionnés (taux plats au départ, mais avec un id).
5. Gold + `recipe_id`.
6. `calibration_run` avec la clé de reproductibilité complète + test "rerun → même input_hash, mêmes paramètres".
7. Collecteur 1 min + scheduler + monitoring.
