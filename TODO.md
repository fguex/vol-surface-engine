# Vol Surface Engine — TODO

## Pipeline cible

```
Europeans (MarketData)
    → calibrateSSVI()          [warm start]
    → θ_initial

Americans (MarketData)
    → AmericanFitter(θ_initial)
        boucle: θ → LocalVol → PDEPricer(american=true) → résidu vs A_mkt
    → θ*  +  σ_loc*(S,t)
```

---

## 1. PDEPricer  `include/pde/PDEPricer.hpp` / `src/pde/PDEPricer.cpp`

- [ ] Payoff initial : `V[j] = max(S[j] - K, 0)` call, `max(K - S[j], 0)` put
- [ ] Conditions aux bords Dirichlet (call : 0 / S·df_q − K·df_r ; put : K·df_r / 0)
- [ ] Correction Dirichlet dans rhs : `rhs[1] -= A.lower[1]*V[0]` et symétrique
- [ ] Boucle Crank-Nicolson : assembler A,B → rhs = B*V → thomas_solve → V_new
- [ ] Flag `american` : après chaque solve, `V[j] = max(V[j], intrinsèque[j])`
- [ ] Vectoriser sur les strikes d'une même maturité (une PDE, N payoffs simultanés)
- [ ] Extraction du prix par interpolation linéaire en `x0 = log(S0/K)`
- [ ] Greeks par différence finie sur la grille finale (delta, gamma)
- [ ] Choix de grille : `x_min/x_max = ±5σ√T`, N=400 pricing / N=50 calibration
- [ ] Test de validation : vol plate → prix BS analytique, erreur < 0.01

---

## 2. AmericanFitter  `include/calibration/AmericanFitter.hpp` / `src/calibration/AmericanFitter.cpp`

- [ ] Interface : prend `vector<OptionQuote>` (américaines) + `SSVIParams θ_initial`
- [ ] Fonction objectif NLopt : θ → `impliedVolSSVI` → `localVolGrid` → `PDEPricer(american)` → résidu
- [ ] Pondération par spread bid-ask inversé (`w = 1 / (ask - bid)`)
- [ ] Même solver NLopt que `calibrateSSVI` (LN_BOBYQA), même bounds sur θ
- [ ] Grille grossière en calibration (N=50, M=50), fine en pricing final (N=400, M=200)
- [ ] Contrainte d'absence d'arbitrage SSVI maintenue (`isArbitrageFree`)
- [ ] Test : warm start européen → fitter américain, vérifier convergence et résidu final

---

## 3. MarketData — généralisations

- [x] Champ `ExerciseType` sur `OptionQuote`
- [ ] Paramétrer le ticker (supprimer le hardcode `aapl_`)
- [ ] Méthode `europeans()` / `americans()` sur `MarketData` pour séparer les quotes

---

## 4. Suite de tests GoogleTest

- [ ] Activer la cible CMake `tests/`
- [ ] Test BS parity call-put
- [ ] Test SSVI round-trip (calibration sur données synthétiques)
- [ ] Test Dupire positivité sur grille complète
- [ ] Test PDEPricer européen vs BS analytique (vol plate)
- [ ] Test PDEPricer américain ≥ européen pour tout (K, T)
- [ ] Test AmericanFitter convergence sur données synthétiques

---

## Ordre d'implémentation recommandé

1. `PDEPricer` européen + validation BS
2. Extension américaine du `PDEPricer` (3 lignes + test ≥ européen)
3. `AmericanFitter` avec NLopt
4. Généralisation `MarketData`
5. Suite GoogleTest
