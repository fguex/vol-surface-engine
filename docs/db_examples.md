# Ce qu'il y a dans la base

Etat au 14 octobre 2026, base `volsurface` sur le Mac mini (PostgreSQL 16.15 +
TimescaleDB 2.30.0). **Deribit uniquement** : options inverses BTC/ETH et
perpétuel. La chaîne SPX/CBOE a été retirée (migration `004_retrait_cboe.sql`).

Le brut n'est pas ici : il est dans le bronze (`data/deribit/bronze/`, fichiers
zstd rejouables, ~1,5 Go et qui grossit de ~20 Go/jour). La base ne contient que
des états échantillonnés, interrogeables — tout ce qu'elle contient est
**re-dérivable du bronze**, donc un rechargement complet répare n'importe quoi.

## Comment regarder soi-même

### La visite guidée

    ssh macmini-ai@macmini-ai.local 'bash ~/vol-surface-engine/ops/db-tour.sh'

Huit sections : vue d'ensemble, couverture, dimension SCD2, une échelle d'options,
couverture des colonnes, perpétuel, DVOL, échéances.

### En SQL direct

Sur le mini :

    cd ~/vol-surface-engine && set -a; . ./.env; set +a
    export PGHOST=127.0.0.1 PGPASSWORD="$POSTGRES_PASSWORD"
    .venv/bin/python -m ... # ou, pour du SQL :
    docker compose exec -T db psql -U vse -d volsurface

Depuis le MacBook, par tunnel SSH (la base n'écoute que sur la boucle locale) :

    ssh -L 15432:127.0.0.1:5432 macmini-ai@macmini-ai.local
    # puis, dans un autre terminal :
    psql -h 127.0.0.1 -p 15432 -U vse -d volsurface

## La sortie réelle

```text
1. Vue d'ensemble
       objet       | lignes | sur_disque 
-------------------+--------+------------
 instrument        |   1557 | 640 kB
 option_mark       |  65149 | 32 kB
 perp_tick         |    544 | 24 kB
 vol_index         |    544 | 24 kB
 schema_migrations |      5 | 32 kB
(5 rows)


2. Couverture temporelle
    objet    |        premier         |        dernier         | heures | instantanes 
-------------+------------------------+------------------------+--------+-------------
 option_mark | 2026-10-13 07:50:00+00 | 2026-10-14 07:00:00+00 |     25 |         272
 perp_tick   | 2026-10-13 07:50:00+00 | 2026-10-14 07:00:00+00 |     25 |         272
 vol_index   | 2026-10-13 07:50:00+00 | 2026-10-14 07:00:00+00 |     25 |         272
(3 rows)


3. Dimension instrument (SCD2 : valid_from / valid_to)
-- versions courantes :
   instrument_name   |  kind  | type |    strike    |   expiry   |       valid_from       |  valid_to  
---------------------+--------+------+--------------+------------+------------------------+------------
 ETH-26MAR27-800-P   | option | P    |   800.000000 | 2027-03-26 | 2026-10-14 07:00:00+00 | (en cours)
 ETH-15OCT26-2460-P  | option | P    |  2460.000000 | 2026-10-15 | 2026-10-14 06:20:00+00 | (en cours)
 BTC-23OCT26-92000-C | option | C    | 92000.000000 | 2026-10-23 | 2026-10-14 06:10:00+00 | (en cours)
 ETH-14OCT26-2300-C  | option | C    |  2300.000000 | 2026-10-14 | 2026-10-14 06:05:00+00 | (en cours)
(4 rows)

-- versions fermees : des instruments nes dans le bronze de la veille et expires depuis
  instrument_name   |   strike    |   expiry   |       valid_from       |        valid_to        
--------------------+-------------+------------+------------------------+------------------------
 ETH-13OCT26-2540-C | 2540.000000 | 2026-10-13 | 2026-10-13 07:50:00+00 | 2026-10-13 08:00:00+00
 ETH-13OCT26-2520-P | 2520.000000 | 2026-10-13 | 2026-10-13 07:50:00+00 | 2026-10-13 08:00:00+00
 ETH-13OCT26-2520-C | 2520.000000 | 2026-10-13 | 2026-10-13 07:50:00+00 | 2026-10-13 08:00:00+00
 ETH-13OCT26-2540-P | 2540.000000 | 2026-10-13 | 2026-10-13 07:50:00+00 | 2026-10-13 08:00:00+00
(4 rows)


spot BTC (index, dernier perp) : 83325.45   spot ETH : 2503.76

4. La chaine d'options, echeance la plus proche, BTC (derniere valeur connue par instrument)
   instrument_name   |   expiry   |    strike    | mark_iv | mark_price |  delta  | open_interest 
---------------------+------------+--------------+---------+------------+---------+---------------
 BTC-15OCT26-74000-P | 2026-10-15 | 74000.000000 |   79.99 |     0.0001 |         |              
 BTC-15OCT26-75000-P | 2026-10-15 | 75000.000000 |   74.11 |     0.0002 |         |              
 BTC-15OCT26-76000-P | 2026-10-15 | 76000.000000 |   68.13 |     0.0002 |         |              
 BTC-15OCT26-77000-P | 2026-10-15 | 77000.000000 |   65.40 |     0.0003 |         |              
 BTC-15OCT26-78000-P | 2026-10-15 | 78000.000000 |   53.08 |     0.0004 |         |              
 BTC-15OCT26-79000-P | 2026-10-15 | 79000.000000 |   52.71 |     0.0007 |         |              
 BTC-15OCT26-80500-P | 2026-10-15 | 80500.000000 |   39.04 |     0.0013 |         |              
 BTC-15OCT26-81000-P | 2026-10-15 | 81000.000000 |   42.52 |      0.001 |         |              
 BTC-15OCT26-81500-P | 2026-10-15 | 81500.000000 |   37.37 |     0.0026 |         |              
 BTC-15OCT26-82000-P | 2026-10-15 | 82000.000000 |   37.48 |     0.0036 |         |              
 BTC-15OCT26-82500-P | 2026-10-15 | 82500.000000 |   34.85 |     0.0054 |         |              
 BTC-15OCT26-83000-P | 2026-10-15 | 83000.000000 |   35.66 |     0.0056 |         |              
 BTC-15OCT26-83500-P | 2026-10-15 | 83500.000000 |   35.47 |     0.0086 | -0.5376 |          13.4
 BTC-15OCT26-84000-P | 2026-10-15 | 84000.000000 |   35.41 |     0.0122 | -0.6593 |          56.0
 BTC-15OCT26-84500-P | 2026-10-15 | 84500.000000 |   33.48 |     0.0134 |         |              
 BTC-15OCT26-85000-P | 2026-10-15 | 85000.000000 |   33.22 |     0.0183 |         |              
 BTC-15OCT26-85500-P | 2026-10-15 | 85500.000000 |   37.02 |     0.0261 |         |              
 BTC-15OCT26-86000-P | 2026-10-15 | 86000.000000 |   36.10 |     0.0333 |         |              
 BTC-15OCT26-86500-P | 2026-10-15 | 86500.000000 |   36.04 |     0.0335 |         |              
 BTC-15OCT26-87000-P | 2026-10-15 | 87000.000000 |   42.47 |     0.0438 |         |              
 BTC-15OCT26-88000-P | 2026-10-15 | 88000.000000 |   46.66 |     0.0555 |         |              
 BTC-15OCT26-89000-P | 2026-10-15 | 89000.000000 |   46.28 |      0.067 |         |              
 BTC-15OCT26-90000-P | 2026-10-15 | 90000.000000 |   44.60 |     0.0779 |         |              
 BTC-15OCT26-91000-P | 2026-10-15 | 91000.000000 |   46.26 |     0.0919 |         |              
 BTC-15OCT26-92000-P | 2026-10-15 | 92000.000000 |   46.31 |     0.1033 |         |              
 BTC-15OCT26-93000-P | 2026-10-15 | 93000.000000 |   44.89 |     0.1143 |         |              
(26 rows)


5. Qui porte quoi : couverture des colonnes de option_mark
 lignes | avec_iv | avec_greeks | avec_bid_ask_iv | avec_open_interest | pct_bande_atm 
--------+---------+-------------+-----------------+--------------------+---------------
  65149 |   65149 |       10968 |           10968 |              10968 |          16.8
(1 row)


6. Perpetuel : funding et open interest (8 derniers points)
      observed_at       | instrument_name | index_price | mark_price | funding_bp | open_interest 
------------------------+-----------------+-------------+------------+------------+---------------
 2026-10-14 07:00:00+00 | BTC-PERPETUAL   |    83325.45 |   83351.56 |      0.634 |     857411320
 2026-10-14 07:00:00+00 | ETH-PERPETUAL   |     2503.76 |     2504.2 |      0.000 |     242266398
 2026-10-14 06:55:00+00 | BTC-PERPETUAL   |    83351.06 |   83375.67 |      0.453 |     856865370
 2026-10-14 06:55:00+00 | ETH-PERPETUAL   |     2502.42 |    2502.87 |      0.000 |     242277727
 2026-10-14 06:50:00+00 | ETH-PERPETUAL   |     2503.32 |    2503.76 |      0.000 |     242264013
 2026-10-14 06:50:00+00 | BTC-PERPETUAL   |    83366.44 |   83390.14 |      0.343 |     856720250
 2026-10-14 06:45:00+00 | BTC-PERPETUAL   |    83347.81 |   83371.86 |      0.386 |     856339670
 2026-10-14 06:45:00+00 | ETH-PERPETUAL   |     2501.95 |    2502.44 |      0.000 |     242253941
(8 rows)


7. Indices de volatilite (DVOL)
      observed_at       | underlying | dvol  
------------------------+------------+-------
 2026-10-14 07:00:00+00 | BTC        | 38.09
 2026-10-14 07:00:00+00 | ETH        | 51.68
 2026-10-14 06:55:00+00 | BTC        | 38.10
 2026-10-14 06:55:00+00 | ETH        | 51.68
 2026-10-14 06:50:00+00 | BTC        | 38.09
 2026-10-14 06:50:00+00 | ETH        | 51.69
(6 rows)


8. Instruments par echeance (les 8 prochaines, BTC, versions courantes)
   expiry   | calls | puts |  strike_min  |  strike_max   
------------+-------+------+--------------+---------------
 2026-10-15 |    21 |   26 | 73000.000000 |  93000.000000
 2026-10-16 |    21 |   25 | 70000.000000 | 100000.000000
 2026-10-17 |    25 |   27 | 74000.000000 |  94000.000000
 2026-10-23 |    20 |   24 | 70000.000000 | 105000.000000
 2026-10-30 |    52 |   54 | 40000.000000 | 115000.000000
 2026-11-27 |    48 |   48 | 58000.000000 | 120000.000000
 2026-12-25 |    44 |   56 | 20000.000000 | 250000.000000
 2027-03-26 |    52 |   52 | 35000.000000 | 200000.000000
(8 rows)
```

Ce qu'il faut y lire :

- **Le smile est là.** Sur l'échéance du 15 octobre, les puts passent de 80,0 % à
  74 000 à un minimum de 33,2 % vers 85 000 (le spot est à 83 325), puis
  remontent à 46,3 % à 92 000. C'est une surface exploitable, pas un tas de
  nombres.
- **16,8 % des lignes portent les greeks** (10 968 sur 65 149). C'est voulu :
  `ticker` ne suit qu'une bande ATM, `markprice` couvre toute la chaîne mais sans
  greeks. Une colonne `delta` vide veut dire « pas dans la bande à cet instant ».
- **Une seule journée et demie** de profondeur (25 h, 272 tranches de 5 min) :
  les canaux IV/greeks n'ont été abonnés que le 13 octobre vers 07:50 UTC.

## Requêtes utiles

Un instantané de la surface, avec la monnaie logarithmique `k = ln(K/F)` :

```sql
WITH d AS (SELECT DISTINCT ON (instrument_sk) instrument_sk, mark_iv, mark_price, index_price
             FROM option_mark ORDER BY instrument_sk, observed_at DESC)
SELECT i.instrument_name, i.expiry, i.strike, i.option_type,
       round(d.mark_iv::numeric, 2) AS iv,
       round(ln(i.strike / d.index_price)::numeric, 4) AS k_logm
  FROM d JOIN instrument i USING (instrument_sk)
 WHERE i.underlying = 'BTC' AND i.expiry = '2026-10-15'
 ORDER BY i.strike, i.option_type;
```

Attention : `markprice.options` est un flux **incrémental**, une tranche de 5 min ne
contient que ce qui a bougé. Pour « toute la chaîne maintenant », il faut donc
prendre la dernière valeur **par instrument** (le `DISTINCT ON` ci-dessus), pas la
dernière tranche.

La structure par terme de la vol ATM (le point de départ d'une première coupe SSVI) :

```sql
WITH d AS (SELECT DISTINCT ON (m.instrument_sk) m.instrument_sk, m.mark_iv, m.index_price
             FROM option_mark m ORDER BY m.instrument_sk, m.observed_at DESC),
     s AS (SELECT DISTINCT ON (i.expiry) i.expiry, i.strike, d.mark_iv
             FROM d JOIN instrument i USING (instrument_sk)
            WHERE i.underlying = 'BTC' AND i.kind = 'option' AND i.expiry > current_date
            ORDER BY i.expiry, abs(i.strike - d.index_price))
SELECT expiry, strike AS strike_le_plus_proche, round(mark_iv::numeric, 2) AS iv_atm
  FROM s ORDER BY expiry;
```

Le funding du perpétuel (la base du carry, à comparer au taux sans risque) :

```sql
SELECT date_trunc('hour', observed_at) AS heure,
       round(avg(10000 * current_funding)::numeric, 3) AS funding_bp_moyen,
       round(max(index_price)::numeric, 0) AS spot_haut
  FROM perp_tick p JOIN instrument i USING (instrument_sk)
 WHERE i.instrument_name = 'BTC-PERPETUAL'
 GROUP BY 1 ORDER BY 1 DESC LIMIT 24;
```

## Pièges à connaître

1. **Toutes les volatilités sont en POURCENTAGE** (35,61 = 35,61 %), jamais en
   fraction. Deribit publie `iv` en fraction dans `markprice.options` et
   `mark_iv` en pourcentage dans `ticker` : les deux ont longtemps atterri dans la
   même colonne (un strike d'aile valait 0,36 « pour cent »). Corrigé le
   14 octobre, et rendu impossible à réintroduire par une contrainte
   (`option_mark_mark_iv_plausible`, migration `005`).
2. **0 et 999 sont des sentinelles**, pas des volatilités : 0 = pas de prix,
   999 = pas de carnet de ce côté (`ask_iv`). À filtrer avant tout calcul.
3. **`mark_price` est en BTC ou ETH**, pas en dollars : ce sont des options
   inverses. Le prix en dollars est `mark_price × index_price`.
4. **`observed_at` est l'horodatage de Deribit**, pas l'heure d'écriture : rejouer
   le même bronze produit les mêmes lignes au bit près.
5. **La dimension `instrument` est en SCD2** (`valid_from` / `valid_to`) : lire
   `WHERE valid_to IS NULL` pour les instruments vivants, ou joindre sans filtre
   pour retrouver un instrument expiré.


## Les vues de lecture : explorer sans réécrire la même requête

Trois requêtes reviennent tout le temps (l'état de la surface, la structure par terme, le
funding). Elles vivent désormais dans la base (migrations `006` et `007`), pas dans un carnet :
DBeaver, `psql` et les scripts Python lisent exactement la même chose.

    v_spot          dernier état du perpétuel par sous-jacent — le spot de référence
    v_surface       une ligne par option : k = ln(K/F), T en années, IV en %, greeks,
                    et reference_du_k (quel prix a servi)
    v_atm_terme     vol ATM par échéance (strike le plus proche du spot)
    v_perp_funding  funding en points de base par 8 h et annualisé, base vs index

Dans DBeaver, tu les trouves sous `public` → `Views`, et tu peux les trier/filtrer comme une
table. Exemple : `v_surface` filtré sur `underlying = 'BTC'`, trié par `t_annees` puis `k_logm`,
te donne la surface complète prête à lire.

**Important — les colonnes `index_price` / `underlying_price` d'`option_mark` sont souvent NULL.**
Le canal `ticker` de Deribit ne suit qu'une bande de strikes autour du spot : au-delà de quelques
jours d'échéance, ces colonnes sont vides. C'est pour ça que les vues prennent le spot sur le
perpétuel (`v_spot`), disponible en continu. Si tu écris ta propre requête, fais pareil — sinon un
« strike le plus proche » retombe sur un strike arbitraire et produit un chiffre faux mais
plausible (vécu : 92,9 % de vol ATM annoncés pour BTC décembre sur un strike à 30 000, marché à
83 700).

## Tracer un graphique

DBeaver Community n'a pas de panneau graphique (c'est une fonction de la version PRO). Le tracé
passe par le script, qui lit les vues et écrit un PNG :

    # depuis le MacBook, tunnel ouvert
    ssh -f -N -L 15432:127.0.0.1:5432 macmini-ai@macmini-ai.local
    cd ~/vol-surface-engine
    export PGHOST=127.0.0.1 PGPORT=15432 PGUSER=vse PGDATABASE=volsurface
    export PGPASSWORD=$(ssh macmini-ai@macmini-ai.local \
        'grep -m1 POSTGRES_PASSWORD ~/vol-surface-engine/.env | cut -d= -f2-')
    ./.venv/bin/python scripts/plot_surface.py --out docs/figures/surface.png

Deux panneaux : le smile en k pour les premières échéances (ronds = puts, triangles = calls) et
la structure par terme de la vol ATM. Options : `--sous-jacent ETH`, `--echeances 6`, `--dsn
"postgresql://vse@127.0.0.1:5432/volsurface"`.


## Ou vivent les objets

Depuis la migration `sql/008_couches.sql`, la base porte elle-meme le decoupage par couches :

    silver.instrument    dimension SCD2 (versions valides dans le temps)
    silver.option_mark   faits : les marques d'options, par tranche de 5 min
    silver.perp_tick     faits : funding, base, open interest
    silver.vol_index     faits : DVOL BTC et ETH
    gold.v_spot          le spot de reference (perpetuel)
    gold.v_surface       la surface : k = ln(K/F), T en annees, IV en %
    gold.v_atm_terme     la structure par terme ATM
    gold.v_perp_funding  le funding annualise
    public.schema_migrations   le registre du migrateur

Dans DBeaver, ce sont deux dossiers de schemas. Tes requetes peuvent nommer les objets sans
prefixe (le `search_path` de la base s'en charge), mais si tu ecris un script destine a
durer, qualifie : `silver.option_mark`, `gold.v_surface`.
