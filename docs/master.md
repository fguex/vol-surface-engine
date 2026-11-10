# Document maître — vol-surface-engine

État au 28 octobre 2026. Tout ce qui est marqué « mesuré » a été lu sur la machine (base du
Mac mini, tests, arbre git), le reste est déduit du code.

---

## 0. TLDR (explain like I'm 5)

- Le projet a deux moitiés qui ne se parlent pas encore : une **usine à données** (qui tourne
  vraiment, 24/7, sur le Mac mini) et un **moteur de calcul** (qui tourne vraiment, mais sur un
  vieux fichier SPX figé).
- L'usine à données collecte en continu les prix d'options BTC et ETH chez Deribit, les archive
  bruts en fichiers compressés (« bronze »), puis les range proprement toutes les 5 minutes dans
  une base de données (« silver ») qu'on interroge avec des vues toutes faites (« gold »).
- Le moteur de calcul sait prendre une grille de volatilités implicites et en sortir 4 nombres
  (ρ, η, γ, ν) qui décrivent toute la surface. Il sait aussi en déduire une volatilité locale et
  pricer des options par équation aux dérivées partielles.
- **Le trou** : personne n'a encore branché la sortie de l'usine sur l'entrée du moteur. Le
  moteur lit des CSV produits par l'ancienne chaîne SPX (supprimée), et la base ne sait pas
  encore stocker un résultat de calibration (tables retirées par la migration 004).
- La chaîne mathématique est complète **sur le papier jusqu'au pricing américain**, mais dans le
  code : SSVI est fait et testé, Dupire et la PDE sont écrits et non validés, le fit américain
  n'existe pas.
- Deux pièges de fond restent ouverts : (1) les options Deribit sont **inverses** (payoff en
  BTC/ETH) alors que tout le moteur C++ raisonne en dollars linéaires ; (2) `θ(T) = ν·T` est une
  hypothèse forte qui a été **mesurée comme fausse** sur SPX (l'erreur ATM croît avec la
  maturité, η se colle à sa borne).

---

## 1. Ce que le système fait aujourd'hui, en une page

| Couche | Ce qu'elle fait | Où elle vit | État mesuré |
|---|---|---|---|
| Collecte | WebSocket Deribit, BTC + ETH, ticks bruts | `python/collect/deribit_ws.py` → `data/deribit/bronze/` | collecteur **healthy depuis 2 semaines** |
| Bronze | JSONL compressé zstd, rotation horaire, append-only | disque du mini | **13 Go** pour 15 jours |
| Silver | échantillonnage 5 min → TimescaleDB | `python/jobs/load_marks.py` → `silver.*` | **1 038 072** marques d'options, **8 905** ticks perp, **8 902** DVOL, **4 453** tranches de 5 min |
| Gold | vues de lecture (surface, ATM, funding) | `sql/008_couches.sql` → `gold.*` | `v_surface` = **3 367** lignes, ATM = **11** échéances/devise |
| Moteur | SSVI → Dupire → PDE Crank-Nicolson | `src/`, `include/`, `apps/` | compile, 2 tests C++ « passent » (dont 1 vide, voir §8) |
| Exploitation | cron, garde-fou, alertes, sauvegardes | `ops/*.sh` | 6 tâches planifiées actives |
| Poste de dev | tunnel SSH + extraction parquet | `scripts/db-tunnel.sh`, `scripts/pull_dev.py` | 2 jours = ~131 000 lignes, 1,4 Mo, 2 s |

**Ce qui n'est mesuré nulle part dans le dépôt** : aucune calibration n'a jamais été écrite en
base depuis le passage à Deribit, et aucune calibration n'a jamais tourné sur les données
Deribit.

---

## 2. Carte du dépôt

    vol-surface-engine/
    ├── python/                  l'usine à données (Python 3.12, uv)
    │   ├── collect/             collecteur WebSocket + outils d'analyse du bronze
    │   ├── jobs/load_marks.py   bronze -> silver
    │   ├── db/                  connexion + runner de migrations
    │   └── io/atomic.py         écriture atomique de fichiers
    ├── sql/                     001..008, migrations numérotées (seule source de vérité du schéma)
    ├── src/ include/            le moteur C++20 (SSVI, Dupire, PDE, BS, IV, Nelder-Mead)
    ├── apps/                    exécutables : prototype de calibration SPX + stubs
    ├── tests/                   tests C++ (5 fichiers) + tests Python (7 fichiers)
    ├── ops/                     exploitation du mini : cron, alertes, sauvegardes, restauration
    ├── scripts/                 tunnel, bootstrap du mini, tirer les données, tracer
    ├── notebooks/               01_premier_smile.ipynb (hors ligne, depuis parquet)
    └── docs/                    le plan, les carnets de phase 1, et ce document

### 2.1 Le moteur C++, fichier par fichier

| Fichier | Rôle | État |
|---|---|---|
| `include/pricing/blackscholes.hpp`, `src/.../blackscholes.cpp` | BS spot + **BS forward/discount** (`callPriceForward`, `vegaForward`) | fait, testé |
| `include/calibration/ImpliedVol.hpp` | inversion prix → vol, retourne un `variant` (valeur ou erreur) | fait, **fragile** (Newton pur, sans encadrement) |
| `include/calibration/SSVI.hpp`, `src/calibration/SSVI.cpp` | θ, φ, w(k,T), dérivées analytiques, conditions d'arbitrage, `calibrateSSVI` (BOBYQA) | fait |
| `src/calibration/FitDiagnostics.cpp` | RMSE global, max, par échéance, ratio η/η_max | fait, **non compilé avant ce mois** (il manquait au CMake) |
| `src/calibration/LocalVol.cpp` | Dupire en coordonnées de variance totale | écrit, non validé |
| `src/pde/Grid.cpp`, `Tridiag.cpp`, `PDEPricer.cpp` | grille log, Crank-Nicolson + Thomas, conditions aux bords, exercice anticipé | écrit, **non validé** |
| `src/opt/NelderMead.cpp` | Nelder-Mead maison | écrit, inutilisé (SSVI utilise NLopt) |
| `src/data/*` | chargement CSV (quotes, spot, rates, divs), courbes de taux/dividendes | lit un format **hérité de la chaîne CBOE** |
| `apps/calibrate_spx_minimal.cpp` | prototype de bout en bout, sortie JSON (`--json-out`) | **le seul runner qui fonctionne** |
| `apps/calibrate_spx_main.cpp` | runner « propre » de phase 1 | **stub** (prose + TODO) |
| `apps/sandbox_main.cpp` | bac à sable | vide |

### 2.2 Le plan de données (SQL)

| Migration | Ce qu'elle fait | Pourquoi c'est là |
|---|---|---|
| `001_schema.sql` | schéma de l'époque CBOE (snapshot, quote_curated, calibration_run, …) | **jamais modifiée** : le runner vérifie son sha256 |
| `002_extensions.sql` | `btree_gist` | nécessaire à la contrainte d'exclusion SCD2 |
| `003_deribit.sql` | `instrument` (SCD2), `option_mark`, `perp_tick`, `vol_index` (hypertables) + compression 7 j | cœur du silver |
| `004_retrait_cboe.sql` | `DROP` de toutes les tables CBOE, y compris `calibration_run` | abandon de SPX le 11 oct. |
| `005_unites_volatilite.sql` | IV en **pourcentage** partout + garde-fou `CHECK` | corrige un bug d'unités silencieux (fraction vs %) |
| `006_vues_exploration.sql` | `v_surface`, `v_atm_terme`, `v_perp_funding` | lecture |
| `007_reference_de_prix.sql` | le spot de référence vient du **perpétuel**, pas de l'option | corrige un ATM faux et crédible (30 000 au lieu de 83 700) |
| `008_couches.sql` | schémas `bronze` / `silver` / `gold` + `search_path` | la couche devient visible dans la base |

---

## 3. Comment les parties interagissent

### 3.1 Le flux de données (ce qui tourne)

    Deribit (wss://www.deribit.com/ws/api/v2)
       │  push, ~140 msg/s, 1 726 instruments
       ▼
    [collector]  python/collect/deribit_ws.py          (conteneur, restart: unless-stopped)
       │  écrit tel quel : {"r": recv_ns, "m": <message>}
       ▼
    data/deribit/bronze/provider=deribit/date=YYYY-MM-DD/HHMMSSZ.jsonl.zst   (13 Go / 15 j)
       │  + events.jsonl (connexions, trous)  + health.json (last_data_at, idle_s, subscribed)
       ▼
    [cron 2-59/5]  ops/mini-load-marks.sh → python -m python.jobs.load_marks
       │  relit le bronze, découpe en tranches de 5 min, écrit en un COPY par fichier
       ▼
    TimescaleDB (conteneur, 127.0.0.1:5432)
       ├── silver.instrument   (dimension SCD2 : valid_from / valid_to)
       ├── silver.option_mark  (hypertable, clé (instrument_sk, observed_at))
       ├── silver.perp_tick    (funding, base, OI)
       └── silver.vol_index    (DVOL)
       ▼
    gold.v_surface / v_atm_terme / v_spot / v_perp_funding     (vues, zéro stockage)
       ▼
    ├── psql / DBeaver (via tunnel SSH 15432)
    ├── scripts/pull_dev.py  → data/dev/*.parquet (+ manifeste : requête exacte + commit)
    └── notebooks/01_premier_smile.ipynb   (travail hors ligne)

    ✂──── coupure : rien n'alimente le moteur C++ ────────────────────────────
    apps/calibrate_spx_minimal  (lit data/csv/<TICKER>/, format hérité)

À noter : le collecteur souscrit bien `quote.*` (top-of-book de toute la chaîne) et
`trades.option.*.100ms`, mais `load_marks` ne charge en base que `markprice.options`
(IV + prix de toute la chaîne) et `ticker.*` (bande ATM, riches en greeks/OI). Le bid/ask
de toute la chaîne existe donc **dans le bronze seulement** — c'est le matériau d'une
pondération par spread, pas encore exploité.

### 3.2 Les invariants qui tiennent le système

1. **Bronze rejouable à l'identique** : on écrit le message brut, sans parsing. Changer d'avis
   sur la curation ne perd rien.
2. **`observed_at` = horodatage du fournisseur**, jamais l'heure d'exécution. C'est ce qui rend
   `load_marks` déterministe et un fit reproductible.
3. **Idempotence** : `ON CONFLICT DO NOTHING` sur `(instrument_sk, observed_at)`. Rejouer une
   plage ne duplique rien — c'est ce qui autorise un rattrapage et une restauration.
4. **SCD2 sur les instruments** : une option naît (première observation dans le bronze), vit,
   meurt (`expiry + 8 h`). Aucun `UPDATE` destructif.
5. **Migrations immuables** : sha256 enregistré ; retoucher un fichier déjà appliqué fait échouer
   le runner (exit 3) avant toute écriture. On corrige par une migration suivante.
6. **Le retard de la base est borné** : tranche de 5 min + cron de 5 min ⇒ ~10 min mesuré.

### 3.3 L'exploitation

    */5  * * * *     mini-stack-guard.sh      remet Colima + la stack si besoin (le mini n'a pas de session graphique)
    */10 * * * *     mini-alerte.sh           veille (ntfy) : sauvegarde, collecte, base, disques
    2-59/5 * * * *   mini-load-marks.sh       bronze -> base
    5    * * * *     mini-backup-launch.sh --bronze-only   bronze -> disque externe
    30   2 * * *     mini-backup-launch.sh    bronze + pg_dump
    0    4 * * 0     mini-verif-restauration.sh            restauration de contrôle hebdomadaire

Deux pièges déjà payés, à ne pas réapprendre : un processus cron n'a pas l'autorisation macOS
d'écrire sur un volume externe (d'où le passage par `ssh localhost`), et modifier le collecteur
exige `docker compose build collector` — le code est **dans l'image**, pas monté.

---

## 4. La chaîne mathématique, maillon par maillon

### 4.1 Conventions

| Symbole | Sens | Unités |
|---|---|---|
| `S` | prix du sous-jacent (ou indice dérivé) | USD |
| `K` | strike | USD |
| `T` | maturité | années, base 365,25 j, échéance à **08:00 UTC** |
| `F` | forward de maturité `T` | USD |
| `k` | log-moneyness `ln(K/F)` | sans dimension |
| `w` | variance totale `σ²T` | sans dimension |
| `θ` | variance totale ATM `w(0,T)` | sans dimension |

Tout le moteur raisonne en `k` et en `w`, jamais en strike : c'est ce qui rend la surface
comparable entre maturités et entre sous-jacents.

### 4.2 Maillon 1 — du prix à la volatilité implicite

Deux cas coexistent dans le dépôt, et **il faut savoir lequel on regarde**.

**Cas A — SPX/CBOE (historique, figé).** Prix mids sur options linéaires, forward par parité
call-put, IV inversée par le moteur :

    C(K) − P(K) = D·(F − K)          régression linéaire de C−P sur K
    ⇒ pente = −D, ordonnée = D·F     (sur les ~10 strikes les plus proches du forward)

Puis Black-76 et Newton protégé. C'est cette chaîne qu'illustre le carnet
`docs/phase1_completion_workbook.md`.

**Cas B — Deribit (la vraie source aujourd'hui).** On **ne réinverse rien** : on prend
`mark_iv` publiée par Deribit et on la range. Deux détails qui ont coûté cher :

- **Unités.** `markprice.options.*` publie `iv` en **fraction** (0,3557), `ticker.*` publie
  `mark_iv` en **pourcentage** (35,61). Les deux arrivaient dans la même colonne : une aile
  valait 0,36 % de vol et l'ATM 35,61 %. Normalisation en pourcentage dans `load_marks._pct`,
  garde-fou `CHECK` dans `sql/005`.
- **Options inverses.** Le payoff d'un call Deribit est `(S_T − K)⁺ / S_T`, réglé en BTC. Le
  prix en dollars est `P_btc × S`. Ce n'est **pas** la même surface qu'une option linéaire, et
  l'IV publiée est celle du modèle inverse (numéraire = la devise).

Le moteur C++ (Black-76, Dupire, PDE) est écrit pour le cas **linéaire**. Tant que la
conversion inverse n'est pas écrite, on peut ajuster SSVI **à la surface d'IV publiée** (ce
qu'un desk crypto fait couramment), mais on ne peut pas passer à Dupire ni à la PDE sans
incohérence de numéraire.

### 4.3 Maillon 2 — le forward, maillon le plus sensible

En actions : `F = S·e^{(r−q)T}`, avec courbes de taux et de dividendes (`RateCurve`,
`DivCurve`, modèle de dividendes affines **partiellement** implémenté).

En crypto : pas de dividende, pas de taux sans risque observable, et un **basis** permanent
entre spot et perpétuel. La bonne source est le marché lui-même :

    F(T) = estimated_delivery_price   (ou le prix du future d'échéance T, ou perp + basis)

État réel : `v_surface.k_logm = ln(K / coalesce(underlying_price, index_perpetuel))`. Autrement
dit **le spot, pas le forward**. Pour `T` court l'erreur est négligeable ; pour `T = 1 an` elle
vaut le basis + le portage du funding, et elle n'est pas constante en `T` — donc elle déforme le
skew exactement là où SSVI est le plus contraint. C'est le premier correctif à faire avant de
croire un fit sur BTC.

### 4.4 Maillon 3 — Black-76

    d₁ = [ln(F/K) + ½σ²T] / (σ√T),      d₂ = d₁ − σ√T
    C = D·[F·N(d₁) − K·N(d₂)],          P = D·[K·N(−d₂) − F·N(−d₁)]
    vega = D·F·√T·φ(d₁)                 (φ = densité normale standard)

Le pricer spot `BSParams{S,K,T,r,q,σ}` reste disponible, mais **toute la chaîne de calibration
passe par la forme forward** : elle ne demande ni taux ni dividende, seulement `(F, D)`.

Astuce utile (et vérifiée) : pour mettre une option en forme forward dans l'API spot, prendre
`S = F` et `r = q = −ln(D)/T`. Alors `S·e^{−qT} = F·D` et `K·e^{−rT} = K·D` : c'est Black-76
exactement.

### 4.5 Maillon 4 — inversion de la vol implicite

`impliedVol(prix, params, type, σ_min, σ_max, tol, itérations)` retourne un `variant` :
`IVResult{σ, itérations}` ou `IVError`.

- Le prix est **strictement croissant** en σ ⇒ une seule racine ⇒ un encadrement suffit.
- Encadrement sans arbitrage (européen) :
  `max(S e^{−qT} − K e^{−rT}, 0) < C < S e^{−qT}` et symétrique pour le put.
- **État du code** : Newton pur, départ au milieu de `[σ_min, σ_max]` (= 2,5 par défaut).
  En aile et sur les maturités courtes, vega → 0 : le pas explose, `DidNotConverge`, ou pire une
  racine fausse. `NoArbitrageViolation` n'est **jamais** retourné aujourd'hui.
- **Correctif écrit et validé** (§ `docs/phase1_completion_workbook.md`) : Newton protégé par
  bissection (on rétrécit l'intervalle à chaque évaluation, on n'accepte un pas de Newton que
  s'il reste dedans), plus le test des bornes d'arbitrage.

### 4.6 Maillon 5 — SSVI (le cœur)

**Paramétrage.** Quatre paramètres `(ρ, η, γ, ν)` :

    θ(T) = ν·T                                       variance totale ATM
    φ(θ) = η / [ θ^γ (1+θ)^{1−γ} ]                  pente de l'aile
    w(k,T) = θ/2 · [ 1 + ρφk + √((φk+ρ)² + 1 − ρ²) ] variance totale
    σ(k,T) = √( w(k,T) / T )

avec `ρ ∈ (−1,1)` (skew), `η > 0` (niveau de courbure/échelle de φ), `γ ∈ (0,1)` (forme de la
décroissance de φ en T), `ν > 0` (pente ATM, et donc `σ_ATM = √ν`, **constante en T**).

Les dérivées `w_k`, `w_kk`, `w_T` sont analytiques (`SSVI.cpp`) — c'est ce qui permet Dupire
sans différences finies.

**Pourquoi `θ = ν·T`.** C'est le choix « 4 paramètres globaux » : peu de données, un fit
robuste, une surface lisse. Le prix payé est fort : la variance ATM est **forcée linéaire en T**,
alors qu'une vraie structure par terme est bombée ou inversée. Mesure faite sur SPX (20 échéances,
11 nœuds en k) : RMSE 1,6 point de vol, mais l'erreur ATM **croît avec T** (0,8 pt à 3 mois,
2,7 pt à 10 mois) et `η` vient se coller à sa borne. Diagnostic : c'est le paramétrage, pas
l'optimiseur (le test synthétique récupère les paramètres à 1e−6, y compris près de la borne).

**Arbitrage (Gatheral–Jacquier 2014).**

- *Théorème 4.1 (calendar spread)* : `θ` croissant (⇒ `ν > 0`), et
  `0 ≤ ∂_θ(θφ) ≤ φ(1+√(1−ρ²))/ρ²`. Pour ce choix de φ, le rapport vaut `(1−γ)/(1+θ) < 1` :
  **la seconde condition est automatique**, il n'y a rien à coder.
- *Théorème 4.2 (butterfly)* : pour tout `θ`,
  `θφ(1+|ρ|) < 4` et `θφ²(1+|ρ|) ≤ 4`. Les suprema sont explicites :
  `sup θφ = η` et `sup θφ² = η²·c(γ)` avec `a = 1−2γ`, `c(γ) = a^a/(1+a)^{1+a}` (et `c = 1`
  pour `γ = ½`).
- D'où la borne utilisée partout dans le code :

      η_max(γ,ρ) = min( 4/(1+|ρ|),  2/√(c(γ)(1+|ρ|)) )

- **Reparamétrage** `η = u·η_max(γ,ρ)` avec `u ∈ [0,01 ; 1−1e−4]` : les deux conditions
  deviennent **une borne de boîte**, ce que BOBYQA sait faire (il refuse les contraintes
  non linéaires). C'est le sens du commit « reparametrize eta so the no-arbitrage constraint is
  a box bound ».
- **Nuance de lecture, importante** : ces conditions sont **suffisantes, pas nécessaires**.
  `u = 1` veut dire « non certifiable par le théorème 4.2 », pas « il y a arbitrage ». Sur SPX,
  le fit réel affichait `u = 0,9999` alors qu'un scan direct de Durrleman donnait `min g = 0,118`
  (franchement positif).

**Objectif et optimiseur.** Moindres carrés **non pondérés en volatilité implicite** sur la
grille commune `(T_i, k_j)` :

    L(ρ,η,γ,ν) = Σ_i Σ_j [ σ_SSVI(k_j, T_i) − σ_marché(k_j, T_i) ]²

NLopt `LN_BOBYQA`, 4 dimensions, bornes `ρ ∈ [−0,99 ; 0,99]`, `u ∈ [0,01 ; 1−1e−4]`,
`γ ∈ [0,01 ; 0,5]`, `ν ∈ [0,001 ; 1]`, `xtol_rel = 1e−8`, `maxeval = 10 000`, départ
`ρ = −0,5, u = … , γ = 0,3, ν = 0,04`.

Conséquences de ce choix, à assumer :
- chaque nœud pèse pareil : une aile à vega quasi nulle compte autant que l'ATM ;
- l'écart est mesuré en vol, pas en variance totale ni en prix : dans les ailes, une erreur de
  1 point de vol est indolore en P&L ;
- un seul jeu de paramètres pour **toutes** les maturités ⇒ aucune liberté par échéance.

### 4.7 Maillon 6 — diagnostic

`diagnoseSSVIFit` (pur, sans I/O) rend : RMSE global, `max|erreur|`, une ligne par échéance,
et `η/η_max`. Ce dernier nombre est le thermomètre : proche de 1, le modèle demande plus de
liberté que le paramétrage n'en donne. C'est lui, et non le résidu, qui dit *pourquoi* le fit
souffre.

### 4.8 Maillon 7 — Dupire, volatilité locale

En coordonnées de variance totale `w(k,T)` (formule de Gatheral), c'est ce qu'implémente
`LocalVol.cpp` :

    σ_loc²(k,T) = w_T / [ 1 − (k/w)·w_k + ¼(−¼ − 1/w + k²/w²)·w_k² + ½·w_kk ]

Garde-fous présents : `w_T < 0`, `w < 1e−6`, dénominateur ≤ 0 ⇒ `NaN`.
**Danger réel** : dans `Tridiag::assemble_cn`, tout `NaN` ou `σ < 1e−4` est silencieusement
remplacé par `1e−4`. Une surface non arbitrageuse localement devient donc un pricing faux **sans
un seul message**. À remplacer par une erreur explicite.

### 4.9 Maillon 8 — PDE Crank-Nicolson

Coordonnée `x = ln(S/K)`, temps à rebours `τ = T − t`, grille uniforme
`x ∈ [−5σ_atm√T, +5σ_atm√T]`, `N = 400` nœuds, `M = 200` pas de temps (prévu : `N = M = 50`
en calibration).

    ∂V/∂τ = ½σ_loc²(x,τ)·V_xx + (r − q − ½σ_loc²)·V_x − r·V

Discrétisation `θ_CN = ½` : `(I − θΔt·L)V^{n+1} = (I + (1−θ)Δt·L)V^n`, tridiagonale, résolue
par Thomas en O(N). Conditions de Dirichlet aux deux bords (dans `rhs` via correction
`rhs[1] −= A.lower[1]·V[0]`), payoff initial `max(±(S−K),0)`, exercice américain par
`V ← max(V, intrinsèque)` après chaque pas. Prix par interpolation linéaire en `x0 = ln(S0/K)`,
delta et gamma par différences finies sur la grille finale.

Ce qui manque pour que ce maillon soit *prouvé* : le test « vol plate ⇒ prix PDE = prix BS
analytique, erreur < 0,01 » (écrit en prose dans `tests/test_pde_pricer.cpp`, pas exécuté), et
la gestion des dividendes discrets (TODO explicite dans le fichier : le modèle affine est
attendu).

### 4.10 Maillon 9 — fit américain

**Inexistant.** Le design prévu (`TODO.md`) : boucle `θ → Dupire → PDEPricer(american=true) →
résidu vs prix américains`, pondération `1/(ask−bid)`, BOBYQA, grille grossière en calibration
et fine en pricing final. Rien de tout cela n'est codé, et — point à garder en tête — sur
Deribit BTC/ETH **il n'y a pas d'options américaines** : ce maillon ne servira que si un
sous-jacent à exercice anticipé entre dans le périmètre.

---

## 5. Les choix et leurs hypothèses

| # | Choix | Raison | Ce que ça coûte |
|---|---|---|---|
| 1 | `θ(T) = ν·T` | 4 paramètres seulement, fit stable, surface lisse | structure par terme ATM forcée linéaire — **mesuré faux** sur SPX (η à la borne, erreur ATM croissante) |
| 2 | `η = u·η_max`, `u ∈ (0,1]` | BOBYQA n'accepte pas de contrainte non linéaire ; l'arbitrage devient une boîte | conditions suffisantes ⇒ `u = 1` sur-interprète « arbitrage » |
| 3 | objectif = moindres carrés en vol, non pondérés | simple, comparable entre maturités | aucun poids vega/spread/liquidité ; les ailes pèsent autant que l'ATM |
| 4 | grille `k` commune = intersection des échéances | un seul jeu de paramètres global, lisible | l'échéance la plus pauvre impose la fenêtre (SPX : `k ∈ [−0,39 ; +0,065]`, aile droite tronquée) ; l'interpolation invente des IV |
| 5 | IV = `mark_iv` Deribit, pas réinversée | on fait confiance à la place, on évite d'inverser des mids bruités | on hérite de leur convention (options **inverses**) et de leurs sentinelles (0, 999) |
| 6 | `k = ln(K/spot)` (perp ou `underlying_price`) | disponible en continu, pas de modèle | ce n'est pas le forward : biais de `k` croissant en `T`, donc skew faussé aux maturités longues |
| 7 | échantillonnage 5 min en base | surface suffisante, base petite, latence bornée à ~10 min | pas de microstructure ; une tranche = dernière valeur connue, pas un vrai snapshot simultané |
| 8 | bronze en fichiers zstd, jamais en base | rejouabilité, 13 Go pour 15 j au lieu de centaines | discipline de sauvegarde, budget disque, et le mini est un point unique de panne |
| 9 | outils à la main plutôt qu'un scheduler dans Docker | cron + scripts = simple à déboguer sur une machine sans session graphique | deux sources de vérité (`crontab` + `ops/`), à garder synchronisées |
| 10 | PDE : CN, grille uniforme en `x`, Dirichlet, Thomas | schéma standard, O(N) par pas, précision au 2e ordre | pas de Rannacher (ringing sur payoff non lisse), pas de grille raffinée près du strike, NaN écrasé à 1e−4 |
| 11 | Nelder-Mead maison conservé | exercice pédagogique | inutilisé : ne pas le remettre en service pour « comparer » avant d'avoir un cas synthétique propre |

---

## 6. Les ruptures de chaîne (par ordre d'impact)

| # | Rupture | Impact | Correctif |
|---|---|---|---|
| G1 | **Aucun lien base → moteur** : `MarketData::load` lit `data/csv/<T>/quotes.csv` + `spot.txt`, format produit par l'exporteur CBOE supprimé | le moteur ne voit jamais les données Deribit | une extraction `gold.v_surface` → CSV/JSON (ou un binding direct), et le contrat d'entrée écrit noir sur blanc |
| G2 | ~~**Pas de forward** : `k` prend le spot~~ — **corrigé le 10/11/2026** : le collecteur souscrit les 24 futures datés (`ticker.*.agg2`), `silver.future_mark` et `gold.v_forward` exposent la structure par terme, `v_surface` porte `forward_usd` / `k_fwd` (migration 009) | reste : les options quotidiennes n'ont pas de future → interpolation côté moteur, et la calibration lit encore `k_logm` | alimenter la calibration avec `k_fwd` + `MarketForwardCurve` (S2). Biais mesuré avant correctif : 0,15 pt de vol à 1,5 mois, 0,45 pt à 10,5 mois |
| G2b | **`quote.*` est collecté mais jamais chargé** : bid/ask de toute la chaîne sont dans le bronze, le silver ne connaît que la bande ATM (`ticker.*`, 20 instruments/devise) | impossible de pondérer un fit par le spread réel sur les ailes | charger `quote.*` dans silver (ou une table dédiée) avant d'introduire un objectif pondéré |
| G3 | **Options inverses non converties** | Dupire et la PDE sont faux en numéraire ; seul l'ajustement d'IV publiée est défendable | écrire la conversion (prix coin → USD, payoff inverse) et choisir explicitement la convention |
| G4 | **Pas de tables de calibration** (retirées par 004, jamais réécrites) | aucun paramètre stocké, aucune comparaison historique possible | nouvelle migration : `calibration_run`, `calibration_slice`, `quote_fit` au format crypto |
| G5 | **Moteur absent de l'image Docker** | pas de calibration planifiée possible | Dockerfile multi-stage (Eigen, NLopt, arm64) |
| G6 | **Dupire/PDE non validés** | on ne sait pas si le pricer est juste | test « vol plate ⇒ BS », puis test américain ≥ européen |
| G7 | **Fit américain absent** | hors périmètre tant qu'il n'y a pas d'américaines | ne pas le commencer avant que G1–G6 soient fermés |
| G8 | **Inversion d'IV fragile** (Newton nu) | les IV réinversées des ailes sont fausses ou absentes | Newton protégé + bornes d'arbitrage (code prêt, carnet de phase 1) |
| G9 | **`a[u]=1` mal interprété** | on conclut « arbitrage » là où le théorème ne certifie rien | scan de Durrleman en diagnostic, et le dire dans le rapport |

---

## 7. État de vérification

| Vérification | Commande | Résultat |
|---|---|---|
| Build C++ | `cmake -S . -B build && cmake --build build -j4` | OK |
| Tests C++ | `ctest --test-dir build` | **2/2 « passent »** — mais `test_ssvi_synthetic` est encore un **stub qui affiche TODO et retourne 0** : un test vert qui ne vérifie rien. `test_implied_vol`, `test_market_data`, `test_pde_pricer` sont des fichiers de prose sans `main`, donc non compilés |
| Tests Python | `./.venv/bin/python -m pytest tests/python -q` | **49 passed, 14 skipped** (les skips sont les tests d'intégration qui exigent une base) |
| Collecte | `docker compose ps` sur le mini | `collector Up 2 weeks (healthy)`, `db Up 2 weeks (healthy)` |
| Volume silver | `select count(*) from silver.option_mark` | **1 038 072** lignes, du 13 oct. 07:50 au 28 oct. 20:50 UTC |
| Couverture | `select count(distinct expiry)` | **27** échéances par devise, jusqu'au 24 sept. 2027 |
| Fraîcheur | `max(observed_at)` vs `now()` | cohérent avec l'invariant ≤ 10 min |
| Disque | `du -sh data/deribit/bronze` / `df -h` | **13 Go** / ~41 Go libres |

---

## 8. Où lire quoi

| Document | Contenu |
|---|---|
| `docs/data_architecture_medallion.md` | bronze/silver/gold, reproductibilité des fits, `valid_from/valid_to`, choix de source mesurés |
| `docs/data_design_deribit_ml.md` | ce que SSVI **exige** de la donnée crypto (forward, options inverses), priorités P0/P1/P2, budget disque |
| `docs/macmini_db_ingestion_plan.md` | installation du mini, état d'exécution 11 oct., les 5 pièges vécus |
| `ops/README.md` | migrations, cron, sauvegardes, pièges d'exploitation |
| `docs/db_examples.md` | requêtes prêtes à l'emploi sur la base |
| `docs/dev.md` | travailler depuis le MacBook (tunnel + parquet) |
| `docs/phase1_ssvi_workbook.md` | carnet d'exercices phase 1 (déjà livré) |
| `docs/phase1_completion_workbook.md` | ce qu'il reste à coder côté moteur + **solutions compilées et testées** |
| `notes/quant_fit_state.md` | état d'esprit du fit au moment du passage à Deribit |

---

## 9. Prochaines étapes, en trois lots

**Lot A — raccorder les deux moitiés (le plus utile, ~2 sessions)**
1. `sql/009_calibration_crypto.sql` : `calibration_run` / `calibration_slice` / `quote_fit` au
   format BTC/ETH (références `instrument_sk`, IV en %, poids).
2. Extraction `gold.v_surface` → fichier d'entrée du moteur, avec `snapshot_id` et
   `input_hash` ; écrire le contrat dans un en-tête de fichier.
3. Moteur dans l'image (multi-stage) + job `calibrate` qui écrit le run en une transaction.
4. Un fit réel BTC et un fit réel ETH, avec diagnostic par échéance : c'est la première
   mesure honnête du modèle.

**Lot B — rendre le moteur croyable**
5. Newton protégé + bornes d'arbitrage (carnet de phase 1), et **vrais** tests C++ (les stubs
   actuels donnent une fausse impression de couverture).
6. Forward d'échéance (futures/`estimated_delivery_price`) ⇒ `k` correct.
7. `θ` libre par échéance (structure par terme ATM lue au marché, contrainte de monotonie)
   — c'est très probablement le correctif qui fera passer la RMSE de « 1,6 point et des
   résidus structurés » à quelque chose d'exploitable.
8. Conversion options inverses, puis validation Dupire + PDE contre Black-76.

**Lot C — industrialiser**
9. Calibration planifiée (par ex. toutes les 15 min), warm start depuis le run précédent.
10. Monitoring : âge du dernier fit, RMSE, `u`, dérive des paramètres.
11. Rétention du bronze et vérification mensuelle de restauration.

Règle de conduite pour la suite : **ne rien calibrer sur BTC avant le point 6** (le forward).
Un fit sur un `k` biaisé est un chiffre faux et crédible — le pire des deux.
