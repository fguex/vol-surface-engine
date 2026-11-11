# Tutoriel des bloquants

*Ce qui empêche d'avancer, pourquoi, et quoi changer — dans l'ordre.*

État au 10 novembre 2026. À lire avec `docs/todos.pdf` ouvert à côté.

---

## 0. Pourquoi un tutoriel séparé

Un backlog dit *quoi* faire. Il ne dit pas *pourquoi ça bloque*, et c'est la seule chose qui
compte quand on reprend un projet trois semaines plus tard.

Ce document prend les tâches **BLOQUANT** une par une, dans l'ordre où elles doivent être faites,
et pour chacune :

1. **ce qui se passe aujourd'hui** — l'état mesuré, pas l'idée qu'on s'en fait ;
2. **pourquoi ça bloque** — ce qu'on ne peut pas mesurer tant que ce n'est pas fait ;
3. **le schéma** — le flux de données avant / après ;
4. **les maths** — étape par étape, avec les formules et l'ordre de grandeur ;
5. **où changer** — fichier, fonction, signature ;
6. **comment savoir que c'est fini** — un chiffre, pas une impression.

L'ordre n'est pas négociable : chaque chapitre utilise le précédent.

```
   D2  test SSVI qui passe a vide.............|  sans eux, « le test passe »
   D3  trois tests jamais compiles............|  ne prouve rien
                                              |
                                              v
   A1  snapshot base -> moteur................|  sans lui, le moteur ne voit
   A3  schema 010 (run, slice, quote).........|  aucun marche reel : tout le
   A4  JSON du run............................|  reste est theorique
   A5  insertion en base......................|
   A7  rejouabilite...........................|
                                              |
                                              v
   B1  quelle convention publie Deribit.......|  sans elle, chaque prix
   B2  le kernel retenu.......................|  calcule est decoratif
                                              |
                                              v
   C2  ecart lineaire vs inverse               -> decide la coordonnee de Dupire
```

---

## 1. D2 — Le test qui passe à vide

### Ce qui se passe aujourd'hui

`tests/test_ssvi_synthetic.cpp` affiche « TODO » et sort avec le code 0. `ctest` annonce donc :

```
Test #3: test_ssvi_synthetic .............. Passed    0.17 sec
100% tests passed, 0 tests failed out of 3
```

Deux tests sur trois existent vraiment. Le troisième occupe une ligne de succès.

### Pourquoi c'est bloquant

Tous les chapitres qui suivent ont un critère du type « les paramètres sont retrouvés », « l'aller-
retour tient à 1e−12 », « le fit reproduit la vue ». Ces critères se vérifient **avec des tests**.
Si le harnais de test est décoratif, chaque critère suivant est une opinion.

C'est aussi le seul endroit où le projet peut te mentir sans que personne ne s'en aperçoive : un
test vert qui ne teste rien est plus dangereux qu'un test absent, parce qu'il occupe la place.

### Ce que doit faire un test d'aller-retour

```
   (rho, eta, gamma, nu)  ──►  w(k,T)  ──►  sigma = sqrt(w/T)  ──►  calibration  ──►  (rho', eta', ...)
        vrais                                                              doit rendre les vrais
```

Le schéma est volontairement pauvre : aucune donnée de marché, aucun forward, aucune IV de place.
Si ce test échoue, le bug est **dans le modèle**, pas dans le pipeline. C'est ce qui en fait un test
utile : il isole une seule cause possible.

### Les maths, étape par étape

**Étape 1.** Choisir des paramètres vrais admissibles. Rappel des contraintes du code :

    rho in [-0.99, 0.99]        eta > 0 (borne haute = eta_max, voir etape 3)
    gamma in [0.01, 0.5]        nu in [0.001, 1.0]

Exemple qui marche : `rho = -0.35`, `eta = 0.70`, `gamma = 0.30`, `nu = 0.045`.

**Étape 2.** Construire la variance totale sur une grille :

    theta(T) = nu * T
    phi(theta) = eta / (theta^gamma * (1+theta)^(1-gamma))
    w(k,T) = theta/2 * ( 1 + rho*phi*k + sqrt( (phi*k + rho)^2 + 1 - rho^2 ) )

**Étape 3.** Vérifier l'admissibilité — c'est le piège de ce test. La borne n'est pas `eta < inf`,
c'est `eta < eta_max(gamma, rho)`, et le code optimise en fait `u = eta / eta_max ∈ [0.01, 1−marge]`.
Si tu prends des paramètres vrais hors domaine, la calibration ne *peut pas* les retrouver et tu
passes une heure à chercher un bug inexistant.

    eta_max = min( 4/(1+|rho|) , 2/sqrt( a*r*(1+|rho|) ) )    avec a=1-|rho|, r=0.5-gamma

Pour (rho, gamma) = (−0.35, 0.30) : `eta_max ≈ 2.62`, donc `eta = 0.70` donne `u ≈ 0.27`, bien à
l'intérieur. Contrôle : `isArbitrageFree(vrais) == true`.

**Étape 4.** Passer de `w` à `sigma` — et pas l'inverse :

    sigma(k,T) = sqrt( w(k,T) / T )

**Étape 5.** Calibrer sur cette matrice d'IV et comparer. Les tolérances doivent refléter `xtol_rel`
= 1e−8 de BOBYQA, pas la précision machine : viser 1e−12 sur `rho` serait un test qui échoue pour
une raison qui n'est pas un bug.

### Où changer

`tests/test_ssvi_synthetic.cpp` uniquement. Rien dans la bibliothèque.

### Comment savoir que c'est fini

```
[ ] le test affiche les parametres retrouves et l'erreur maximale
[ ] il echoue si l'on inverse deux parametres a la main (controle negatif)
[ ] la tolerance est justifiee par xtol_rel, ecrite en commentaire
[ ] ctest reste a 3/3, mais 3 tests qui testent quelque chose
```

Le **contrôle négatif** est la partie qu'on oublie toujours : un test qui ne peut pas échouer n'est
pas un test. Inverse `rho` et `gamma` dans la comparaison, vérifie qu'il casse, puis remets.

---

## 2. D3 — Trois fichiers de test jamais compilés

### Ce qui se passe aujourd'hui

```
tests/test_implied_vol.cpp      pas de main()  ->  jamais compile
tests/test_market_data.cpp      pas de main()  ->  jamais compile
tests/test_pde_pricer.cpp       pas de main()  ->  jamais compile
```

Le CMake ne construit que les fichiers contenant `int main(`. Ces trois-là sont dans le dépôt,
donnent l'impression d'une couverture, et ne sont jamais exécutés.

### Pourquoi c'est bloquant

`ImpliedVol` a déjà été corrigé une fois (bornes d'arbitrage, bracket). Le correctif n'est protégé
par **aucun** test. La prochaine personne qui « simplifie » le solveur peut supprimer le bracket,
et rien ne le dira — c'est exactement ce qui s'était passé avec le Newton nu.

### Où changer

Ajouter un `main()` à chacun, sur le modèle de `test_pipeline_synthetic.cpp` :

```
int main() {
    // 1. cas nominal
    // 2. cas limite (aile, T court)
    // 3. cas qui doit echouer (prix sous l'intrinseque -> NoArbitrageViolation)
    return failures == 0 ? 0 : 1;
}
```

Le troisième cas est celui qui compte : il vérifie que l'erreur est **distinguée**, pas seulement
qu'elle existe.

### Comment savoir que c'est fini

```
[ ] ctest liste 6 tests (3 existants + 3 nouveaux)
[ ] chacun contient au moins un cas qui echoue si l'on casse la fonction expres
```

---

## 3. A1 — Le snapshot : le seul endroit où la base entre dans le moteur

### Ce qui se passe aujourd'hui

```
   base TimescaleDB                    moteur C++
   ─────────────────                   ──────────
   silver.future_mark ─┐
   silver.option_mark ─┼─► gold.v_surface ──►  ... rien ...
   gold.v_forward    ──┘

   data/csv/SPX/quotes.csv ──────────────►  MarketData::load  ──►  calibration
   (un fichier fige, sans forward, sans lien avec la base)
```

Le moteur ne sait pas lire la base, et la base ne sait pas produire ce que le moteur attend. Les
deux moitiés du projet ont chacune des tests verts, et ne se sont jamais parlé.

C'est **le** blocage : tout le reste (stockage du run, rejouabilité, convention de prix, PDE) suppose
un objet `SurfaceSnapshot` qui vienne réellement du marché.

### Le flux après A1

```
   gold.v_surface ──┐
   gold.v_forward ──┼─► export_snapshot.py ──► data/snapshots/<id>/{meta,forward,quotes}.csv
                    │                                                          │
                    │                                                          ▼
                    │                                          SnapshotFile::load (I/O)
                    │                                                          │
                    │                                                          ▼
                    │                                        SnapshotBuilder (calcul pur)
                    │                                                          │
                    ▼                                                          ▼
              (audit / requetes)                                       SurfaceSnapshot
```

### Les maths, étape par étape

**Étape 1 — le moneyness.** `k = ln(K / F(T))`, et non `ln(K/S)`. Pourquoi : mesuré sur BTC le
10/11, la base vaut 489 bp à 10,5 mois, et l'erreur de skew qu'elle induit est de 0,45 point de vol.
Le forward est celui de l'échéance, lu sur les futures (`gold.v_forward`).

**Étape 2 — combler les échéances sans future.** Deribit cote des options quotidiennes mais des
futures seulement sur les échéances hebdomadaires/mensuelles/trimestrielles. Entre deux nœuds :

    ln F(T) = ln F(T1) + (T - T1)/(T2 - T1) * (ln F(T2) - ln F(T1))

C'est un **carry constant par morceaux** : `c = ln(F/S)/T` est constant entre deux nœuds. Extrapolation
plate au-delà du dernier nœud, et on tronque la grille T plutôt que d'inventer un forward.

**Étape 3 — choisir le côté.** À chaque strike, on garde l'option la moins chère en valeur relative,
c'est-à-dire :

    k > 0  ->  call   (OTM)
    k < 0  ->  put    (OTM)

Raison : le prix d'une option profonde dans la monnaie est dominé par l'intrinsèque, et la
volatilité implicite y est numériquement instable (vega → 0). Le côté OTM garde la totalité de la
valeur temps.

**Étape 4 — la grille commune.** SSVI ajuste `(rho, eta, gamma, nu)` **simultanément** sur toutes
les maturités : il faut donc une seule grille `k` pour toutes. On prend l'intersection des plages
couvrées, puis on rééchantillonne :

    k_grille = k_lo + j * (k_hi - k_lo)/(N-1),  j = 0..N-1,  N = 11 (comme le runner SPX)

**Étape 5 — l'interpolation, en variance totale et non en vol.** C'est le point le plus important
de l'étape, et celui qui se rate le plus souvent :

    w(k,T) = sigma(k,T)^2 * T

On interpole `w` linéairement en `k`, puis on revient à `sigma = sqrt(w/T)`. Pourquoi : `w` est
presque linéaire en `k` (SVI est une hyperbole en `k`), `sigma` ne l'est pas. L'erreur d'une
interpolation linéaire est en `O(h² * w'')` — sur `sigma`, la dérivée seconde est bien plus grande,
et l'erreur se concentre dans les ailes, exactement là où le fit est déjà fragile.

**Étape 6 — les poids.** `w_ij = 1 / vega(k_i, T_j)`, normalisés par leur moyenne (voir le chapitre
sur les poids : le calibrateur les consomme, il ne les choisit pas).

### Où changer

```
include/model/SurfaceSnapshot.hpp      contrat : deja fait (check(), 11 cas testes)
include/data/SnapshotFile.hpp          le LECTEUR : trois CSV -> structures brutes
src/data/SnapshotFile.cpp
include/model/SnapshotBuilder.hpp      le CONSTRUCTEUR : pur, sans I/O
src/model/SnapshotBuilder.cpp
python/jobs/export_snapshot.py         la base -> le dossier du snapshot
```

Règle de répartition, à ne pas mélanger : `data/` fait de l'entrée/sortie, `model/` fait du calcul.
Le builder ne filtre rien (la propreté est garantie par la base) ; ses seuls contrôles sont les
`assert` du contrat.

### Comment savoir que c'est fini

```
[ ] k recalcule en C++ = k_fwd de la vue, a 1e-12, sur les echeances a future
[ ] 11 echeances et >= 3000 quotes sur BTC
[ ] un test synthétique : forward connu -> F retrouve, paramètres retrouves
[ ] check() == Ok sur le snapshot produit, et le test refuse un snapshot casse
```
