# Environnement de dev, sur le MacBook

## En clair

La base ne vit que sur le mini, et elle n'ecoute que sur la boucle locale du mini : elle est
donc injoignable depuis le MacBook, par construction. Tout le montage tient alors en une
phrase : un tunnel SSH rend cette boucle locale visible ici, et un script tire ce dont on a
besoin dans des fichiers **parquet locaux**. VS Code ne travaille ensuite que sur ces fichiers.
Reseau debranche compris.

Trois commandes, et on est pret :

    scripts/db-tunnel.sh start
    ./.venv/bin/python scripts/pull_dev.py --jours 2
    code .                 # puis notebooks/01_premier_smile.ipynb

## Les pieces

| piece | role |
|---|---|
| `scripts/db-tunnel.sh` | `ssh -L 15432 -> mini:5432`. `start`, `status`, `stop`. Keepalive : le Wi-Fi du mini s'endort. |
| `scripts/pull_dev.py` | Lit `silver.option_mark`, `silver.perp_tick` et les instantanes gold, ecrit `data/dev/*.parquet` et un `manifeste.json` (requete exacte, comptes, commit local). |
| `notebooks/01_premier_smile.ipynb` | Le point d'entree : lit le parquet, trace un smile, sans toucher a la base. |
| `.vscode/settings.json` | Interpreteur `.venv`, pytest sur `tests/python`, connexion SQLTools vers `127.0.0.1:15432`. |
| `.vscode/tasks.json` | Cmd+Shift+B tire les donnees ; les taches couvrent tunnel, tirage, tests, ruff. |
| `.vscode/launch.json` | Debug du tirage et du fichier pytest courant. |

## Ce que le tirage contient, et ce qu'il ne contient pas

`marches.parquet` : une ligne par option et par tranche de 5 min — `observed_at`,
`instrument_name`, `underlying`, `expiry`, `option_type`, `strike`, `mark_iv`, `mark_price`,
`bid_iv`, `ask_iv`, `delta`, `vega`, `open_interest`. Deux jours font ~131 000 lignes et
1,4 Mo ; le tirage prend deux secondes.

**Les vues gold sont des instantanes, pas des series.** `v_surface` renvoie la surface du
dernier instant (2 586 lignes), `v_atm_terme` les 22 echeances du moment. L'historique de la
surface se tire donc de `silver.option_mark`, ce que fait `pull_dev.py`.

Le manifeste porte la requete exacte et le commit : un resultat peut donc toujours etre
ramene a ce qui l'a produit, y compris quand le contenu de la base aura change.

## Regles

- **La base du mini est en production.** On lit, on ne scanne pas tout, on ne calcule rien de
  lourd dessus. Les gros calculs se font en local, sur le parquet.
- **`data/dev/` n'est pas versionne** (voir `.gitignore`). Un extrait se regenere, il ne se
  commit pas.
- **Identifiants** : `pull_dev.py` lit `POSTGRES_PASSWORD` dans `.env` (jamais dans le depot,
  `chmod 600`). Si le message dit « mot de passe refuse », le `.env` local est perime :
  `scp macmini-ai@macmini-ai.local:~/vol-surface-engine/.env .env`.
- **Quand la replique en lecture seule existera**, `pull_dev.py` pointera dessus sans rien
  changer d'autre que `--port`. C'est la que doivent aller les tirages, pas sur le primaire.

## Depannage

| symptome | cause | geste |
|---|---|---|
| « le tunnel est-il demarre ? » | rien n'ecoute sur 15432 | `scripts/db-tunnel.sh start` |
| « le tunnel repond, mais le mot de passe est refuse » | `.env` local perime | recopier le `.env` du mini |
| tirage lent | Wi-Fi du mini endormi | cable Ethernet sur le mini, ou relancer le tunnel |
