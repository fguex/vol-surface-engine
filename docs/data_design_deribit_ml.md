# Design données — Deribit BTC/ETH, DB temps réel, et préparation ML

Statut : proposition à valider. Tous les chiffres marqués « mesuré » viennent d'un run réel
(collecteur du 8 oct. 2026 + sondage API du 10 oct. 2026). Rien n'est estimé sans le dire.

---

## TLDR (explain like I'm 5)

- Aujourd'hui on enregistre à peu près « qui demande quoi » sur Deribit : le meilleur prix
  acheteur/vendeur de chaque option, en continu. C'est utile, mais ça ne suffit PAS à
  construire une surface de volatilité.
- Il manque trois choses, dans cet ordre : (1) le *forward* — en crypto il n'y a pas de
  dividende ni de taux, donc la seule façon honnête de savoir où est le centre de la
  surface est de le lire dans les futures/perpetuals ; (2) la volatilité implicite,
  l'open interest et le volume par instrument (Deribit les donne, mais pas sur le canal
  qu'on écoute) ; (3) l'index de volatilité DVOL, qui est le « VIX crypto » et un
  excellent point de comparaison.
- Point clé : les options BTC/ETH de Deribit sont *inverses* (le premium est payé en BTC,
  pas en dollars). Ça n'empêche rien, mais ça doit être écrit noir sur blanc dans le
  pricer, sinon on comparera des pommes et des poires.
- Pour le ML : la donnée qu'on collecte maintenant est la SEULE qu'on ne pourra pas
  racheter plus tard. DVOL (depuis déc. 2021), funding (depuis au moins 2022) et les prix
  de livraison des futures sont rachetables ; une surface d'options à la minute ne l'est
  pas (vérifié : l'API REST ne renvoie plus les trades de 2022-2023). Donc la décision
  utile aujourd'hui n'est pas « quelle DB », c'est « est-ce qu'on enregistre la grille à
  1 minute à partir de maintenant ».
- Prediction markets : c'est un projet à part, SAUF un morceau : les marchés « BTC
  au-dessus de X le <date> » sur Polymarket/Kalshi sont des options digitales sur le même
  sous-jacent. Les comparer à la densité implicite de Deribit touche directement ta
  recherche (densité neutre au risque, masse en zéro). Coût : quelques Mo. À faire tôt
  parce que la donnée est volatile et non rachetable.

---

## 1. Ce qu'on collecte aujourd'hui (mesuré)

Source : 10 fichiers bronze du 8 oct. 2026 (10,14 h de collecte), et le log du collecteur.

    débit global      : 1 893 958 messages / 10,14 h  = 186 729 msg/h ≈ 52 msg/s
    taille brute      : 520 MiB  (288 octets/message)
    taille compressée : 39,2 MiB (21,7 octets/message, zstd niveau 6, ratio 13,3x)
    projection        : 93 MiB/jour  ≈ 34 Go/an  (au rythme actuel)
    instruments       : 1 764 options (BTC 930 + ETH 796 au 10 oct.)

Répartition par canal (log du collecteur) :

    quote.<instrument>                     1 899 769   99,1 %
    deribit_price_index.<btc|eth>_usd         16 090    0,8 %
    trades.option.<BTC|ETH>.100ms                533    0,03 %

Contenu réel de chaque canal (vérifié dans le bronze) :

| canal | champs | ce qui MANQUE |
|---|---|---|
| `quote.<instr>` | best_bid/ask price + amount, instrument_name, timestamp | IV, greeks, open interest, volume |
| `trades.option.*` | price, amount, direction, iv, mark_price, index_price, trade_seq | — (riche, mais très rare) |
| `deribit_price_index.*` | index_name, price, timestamp | — |

Conséquence : on a un excellent carnet d'ordres *top-of-book* et presque aucune information
de surface. Un fit SSVI a besoin de prix ou d'IV par strike/échéance ET d'un forward : les
deux manquent ou sont incomplets aujourd'hui.

---

## 2. Ce que le modèle SSVI exige (contraintes dures, pas des préférences)

1. **Forward observable.** En actions on construit F = S·e^{(r-q)T} avec une courbe de taux et
   de dividendes. En crypto : pas de dividende, pas de taux sans risque observable, et un
   *basis* permanent entre spot et perp. La bonne source de F est donc le **marché des
   futures/perp Deribit** (13 échéances BTC, 13 ETH, + perpetual), pas un modèle.
   Sans ça, θ(T) et le skew sont faussés d'un facteur non constant.
2. **Options inverses.** Les options BTC/ETH de Deribit sont réglées et cotées en coin
   (`settlement_currency = BTC` ou `ETH`, mesuré). Le payoff d'un call inverse en BTC vaut
   (S_T − K)⁺ / S_T ; en dollars, le prix se ramène à P_usd = P_btc × S. Ce n'est PAS la
   même surface qu'une option linéaire : le passage doit être explicite dans le code
   (convention, pas un détail d'implémentation). Options linéaires USDC : Deribit n'en
   propose que sur des altcoins (mesuré : 3 780 options USDC, aucune BTC_USDC dans
   l'échantillon) — donc on reste en inverse et on assume.
3. **Européennes uniquement.** Bonne nouvelle : pas d'exercice anticipé, le pricer actuel
   (BS forward-based, exercice européen) reste valide une fois la conversion inverse faite.
4. **Grille temporelle régulière.** Un fit SSVI par snapshot, à cadence fixe (60 s), avec
   des `snapshot_id` déterministes (déjà le cas côté CBOE). Une grille irrégulière rend
   les features ML inutilisables sans interpolation — et toute interpolation est un
   couteau dans le dos au moment d'évaluer un modèle.
5. **Historique long.** Une surface minute est un échantillon de taille 1 440/jour ; ce qui
   limite le ML n'est pas le nombre de lignes, c'est le nombre de *jours*.

---

## 3. Architecture cible (3 couches, déjà à moitié en place)

    L0  bronze   : fichiers zstd jsonl append-only, rejouables à l'identique (EXISTE)
    L1  curated  : TimescaleDB — snapshots, cotations typées, courbe de forward,
                   runs de calibration (schéma 001 existe pour CBOE, à étendre crypto)
    L2  features : vues/tables point-in-time + labels calculés (à créer)

Règle : le brut ne va jamais directement en DB ; la DB porte le *typé* et le *dérivé*.
Le brut reste la source de vérité réjouable (et c'est ce qui permet de changer d'avis sur
la curation sans perdre l'historique).

---

## 4. Ce qu'on ajoute, par priorité

### P0 — indispensable pour que la surface existe (coût disque négligeable)

| # | Donnée | Source | Cadence | Volume |
|---|---|---|---|---|
| 1 | `get_book_summary_by_currency` (BTC + ETH, kind=option) : mark_iv, open_interest, mark_price, bid/ask, volume, underlying_price, `estimated_delivery_price` | REST | 60 s | ~155 Ko typé/snapshot → ~17 Mo/j comprimé |
| 2 | Futures + perp : top-of-book des 13 échéances BTC et ETH (dont PERPETUAL) | WS `quote.*` | continu | < 1 Mo/j |
| 3 | Funding history (`interest_1h`, `interest_8h`) | REST | horaire | ~100 Ko/j |
| 4 | DVOL OHLC (BTC + ETH) | REST | horaire | négligeable |
| 5 | Prix de livraison des futures (`get_delivery_prices`) | REST | quotidien | négligeable |

Note importante sur #1 : `underlying_index` de la ligne book_summary donne la *date
d'échéance utilisée par Deribit pour le forward* (`BTC-12OCT26`) et
`estimated_delivery_price` le forward implicite. C'est exactement le F(T) dont on a besoin,
fourni par la place — meilleur qu'un modèle.

### P1 — utile pour la recherche et le ML, volume encore raisonnable

6. **Fenêtre de trades par instrument** : le canal `trades.option.*` agrégé à 100 ms est
   déjà là ; ajouter `trades.option.*` en mode « trade by trade » sans agrégation si on
   veut le micro-timestamp (aujourd'hui on perd la granularité sous 100 ms).
7. **Spot multi-venue** (Binance/OKX/Kraken bookTicker) : réplique du prix BTC/ETH,
   base de features de microstructure et détection de staleness. ~10–50 Mo/j selon la
   sélection d'instruments.
8. **Vues de monitoring** (âge du dernier snapshot, taux stale, rows/snapshot) — déjà
   prévues dans le plan Mac mini, Phase 4.

### P2 — seulement si un besoin précis apparaît (coûteux)

9. **Carnet L2** (`book.<instr>` sur 1 726 instruments) : c'est le poste qui casse le
   budget disque — ordre de grandeur **centaines de Mo à quelques Go par jour**. À ne faire
   que sur un sous-univers (ATM ±20 % des 2 échéances les plus proches, ~60-100
   instruments) ou en profondeur réduite (5 niveaux) et 1 Hz.
10. **Options USDC sur altcoins** : bruit pour ce projet, à ignorer.

---

## 5. Budget disque (Mac mini : 100 Go libres sur 926 Go, mesuré)

    aujourd'hui (tick options)               93 Mo/j     ≈ 34 Go/an
    + P0 complet                            ~110 Mo/j     ≈ 40 Go/an
    + P1 complet                            ~160 Mo/j     ≈ 58 Go/an
    + L2 complet (P2)                    ~1-3 Go/j        ≈ 365-1100 Go/an  ← ne rentre pas

Prévision de rétention à valider (Timescale compression + `drop_chunks`) :

- bronze brut : 90 jours glissants (rejouable, re-téléchargeable pour DVOL/funding uniquement)
- curated + features : indéfini, compressé
- à 40-58 Go/an, le disque actuel tient ~12-18 mois sans intervention. À surveiller via la
  vue de monitoring + alerte disque > 80 %.

---

## 6. Prérequis ML (les quatre qui se paient plus tard si on les oublie)

1. **Point-in-time strict.** Chaque ligne porte `observed_at` + `snapshot_id` ; aucun
   forward-fill en base. Un trou est un trou. Un label ne peut lire que des lignes
   antérieures à son propre `observed_at`.
2. **Migrations versionnées** (plan Phase 1b) AVANT le premier dataset : sans
   `schema_migrations` + `engine_git_sha`, un jeu d'entraînement n'est pas reproductible.
3. **Labels calculables depuis notre propre flux** (pas de source externe) :
   - variance réalisée de l'index sur la maturité (1 h, 24 h, 7 j) ;
   - paramètres SSVI du snapshot suivant (θ, skew, courbure, ρ, ν, η) — cible naturelle
     pour « prédire la surface » ;
   - mouvement du mid à 5 min, déséquilibre de trades, franchissement de spread.
4. **Séparation features/cibles explicite** dans le schéma (`surface_state` vs
   `label_*`), pas dans le notebook. Sinon on refait le même travail à chaque expérience.

Ce qu'on peut racheter plus tard (donc PAS urgent) : DVOL horaire depuis déc. 2021,
funding depuis au moins juin 2022, prix de livraison des futures. Ce qu'on ne peut PAS
racheter : les surfaces à la minute, l'open interest par strike, le carnet.
→ **D'où l'argument pour commencer la collecte P0 maintenant, même imparfaite.**

À creuser : `history.deribit.com` existe (il répond sur `/api/v2/...`) et semble être
l'infra d'archives de Deribit ; la documentation de ce service n'a pas été trouvée en
30 minutes. Ça vaut un appel direct au support Deribit : si les fichiers historiques
(book/trades par instrument) sont accessibles, on gagne des années d'historique d'un coup.

---

## 7. Prediction markets — cadrage honnête

Sondage du 10 oct. 2026 (toutes ces routes répondent 200, échantillons vérifiés) :

| source | route | contenu | auth |
|---|---|---|---|
| Polymarket | `gamma-api.polymarket.com/markets?active=true&closed=false` | question, conditionId, clobTokenIds, endDate, bestBid/bestAsk, liquidity | non |
| Polymarket | `clob.polymarket.com/markets` (paginé, 1 000/lot) | métadonnées de marché | non |
| Polymarket | `clob.polymarket.com/book?token_id=…` / `prices-history?market=…&fidelity=60` | carnet, historique de prix | non |
| Kalshi | `api.elections.kalshi.com/trade-api/v2/markets?limit=…` | bid/ask oui-non, open interest, close_time, event_ticker | non (lecture) |
| Kalshi | `…/trade-api/v2/series?category=Crypto` | catalogue de séries | non |
| Manifold | `api.manifold.markets/v0/markets` | marché play-money | non |
| Metaculus | `api2/questions/` | 403 sans token | oui |

Piège réseau **mesuré** : le DNS du réseau local (192.168.1.1) ne résout PAS
`gamma-api.polymarket.com`, `clob.polymarket.com`, ni `api.elections.kalshi.com`
(probablement filtrage routier « gambling »). Contournement vérifié : requêtes en
DNS-over-HTTPS (`curl --doh-url https://cloudflare-dns.com/dns-query`) → 200. Le
collecteur devra donc résoudre en DoH, ou il faudra lever le filtre sur le routeur.

Verdict :

- **À faire tôt, volume négligeable** : un snapshot horaire des marchés liés au prix
  BTC/ETH (« BTC above X on <date> » sur Polymarket, séries crypto Kalshi). Ce sont des
  **options digitales** : leur prix est une densité implicite, comparable à celle des
  options Deribit. Table proposée :
  `pm_digital(pm, market_id, ts, underlying, strike, expiry, prob_yes, bid, ask, liquidity)`.
  C'est directement raccordable à la recherche (masse en zéro, densité neutre au risque)
  et ça donne au ML une feature exogène que personne n'a.
- **À ne pas faire maintenant** : le reste des prediction markets (élections, sport,
  calibration, arbitrage inter-plateformes) est un projet à part entière : cadence
  différente, autre littérature, autre question. À garder en réserve comme sujet
  distinct, pas comme sous-dossier du pipeline crypto.

---

## 8. Décisions à prendre

1. Cadence de la grille : 60 s (proposé) ou 5 min ? 60 s coûte ~17 Mo/j, le choix
   « 5 min » divise tout par 5 mais tue les features de microstructure.
2. Univers : BTC + ETH seuls, ou BTC + ETH + les 2-3 altcoins les plus liquides (SOL,
   XRP…) ? Chaque ajout multiplie le bronze options.
3. Conversion inverse : on modélise en USD (P_usd = P_btc × S) ou on garde le numéraire
   coin et on adapte la fonction de perte ? Choix structurant pour le portage C++.
4. L2 : on renonce, ou on fait un sous-univers réduit ?
5. Prediction markets crypto : on démarre l'échantillonnage horaire maintenant ?
6. Bronze : politique de rétention (90 jours proposés) et faut-il garder le brut complet
   ou seulement les canaux P0/P1 ?
