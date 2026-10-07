# Phase 1 Completion Workbook — SSVI: data acquisition and calibration

Follow-up to docs/phase1_ssvi_workbook.md. Part A = exercises (goal, hints, pseudocode). Part B = full solution, compiled and tested.

## Acceptance criterion for phase 1

`ctest --test-dir build --output-on-failure` passes 4/4, and `./build/calibrate_spx_main` calibrates SPX repeatably, printing forwards, parameters, eta/eta_max and per-slice errors (ATM vs wings).

## Already done (no action needed)

- Python pipeline: fetch → curate → export quotes.csv / spot.txt, TimescaleDB, Docker.
- SSVI.cpp: formulas, bounded eta reparametrization (u ≤ 1), BOBYQA.
- FitDiagnostics.cpp: implemented.
- CMakeLists.txt (fixed): FitDiagnostics.cpp is in the lib; every apps/*.cpp and tests/test_*.cpp that contains `int main(` is compiled automatically; tests are registered with ctest (assert stays active in Release).

Build / test:

    cmake -S . -B build && cmake --build build -j4
    ctest --test-dir build --output-on-failure
    ./build/calibrate_spx_main data/csv/SPX 2026-09-12

## Order

1. test_ssvi_synthetic (proves the fitter)
2. ImpliedVol (robust inversion) + test_implied_vol
3. ImpliedForward (parity-implied forward) + MarketData (malformed rows counted) + test_market_data
4. calibrate_spx_main (runner)
5. Read the results (section 6)

Rule: run ctest after every exercise.

---

# PART A — EXERCISES

## Exercise 1 — tests/test_ssvi_synthetic.cpp

Goal: if the data follow SSVI exactly, calibrateSSVI must recover the parameters. Without this test, no conclusion drawn from SPX is valid.

Hints
- Do not rely on `assert` alone: write a `check(cond, msg)` that counts failures and `return 1` at the end (same pattern as test_blackscholes.cpp). ctest relies only on the exit code.
- Use a grid that looks like production: 11 k-nodes in [-0.30, 0.25], T from 0.10 to 2.
- Test TWO cases: interior parameters (tol 1e-6), and parameters at u = 0.95 of eta_max (tol 1e-5). The second one proves the fitter is not artificially pinned to u = 1.
- To build the near-bound case: eta = 0.95 * min(4/(1+|rho|), 2/sqrt(phiSq(gamma)*(1+|rho|))), with phiSq(g) = a^a/(1+a)^(1+a), a = 1-2g.
- Add a negative test: a matrix of the wrong size must return SSVIError.

Pseudocode

    check(cond, msg): if !cond → failures++, print msg
    synthSurface(p): iv(i,j) = impliedVolSSVI(k_j, T_i, p)
    roundTrip(name, truth, tol):
        iv  = synthSurface(truth)
        res = calibrateSSVI(k, T, iv)
        check res holds SSVIParams, else return
        d = diagnoseSSVIFit(k, T, iv, fit)
        check |fit.x - truth.x| < tol  for x in {rho, eta, gamma, nu}
        check d.rmse_total < 1e-8 ; 0 < d.eta_ratio < 1 ; d.slices.size() == nT
    main: roundTrip(interior, 1e-6) ; roundTrip(near-bound, 1e-5) ; bad dims → SSVIError
          return failures ? 1 : 0

## Exercise 2 — src/calibration/ImpliedVol.cpp + tests/test_implied_vol.cpp

Problem with the current code: pure Newton started at the midpoint of [sigma_lo, sigma_hi] (= 2.5 by default). In the wings and on short maturities vega ≈ 0, so the Newton step explodes or goes negative → DidNotConverge, or worse, a wrong root. Prices outside the no-arbitrage bounds are not detected (the `NoArbitrageViolation` error is never returned).

Hints
- Price(sigma) is strictly increasing → one unique root, and [lo, hi] is a bracket iff f(lo) ≤ 0 ≤ f(hi).
- Safeguarded Newton: after each evaluation, shrink [lo, hi] using the sign of f; accept the Newton step only if it lands strictly inside (lo, hi), otherwise bisect. Guaranteed convergence.
- Model-free European bounds (strict):
  call: max(S e^{-qT} − K e^{-rT}, 0) < C < S e^{-qT}
  put : max(K e^{-rT} − S e^{-qT}, 0) < P < K e^{-rT}
- Error mapping: bad input → InvalidInput; outside bounds → NoArbitrageViolation; root outside [lo, hi] or maxIter → DidNotConverge.
- Test trap 1: deep ITM with short T, the time value (price − intrinsic) is below 1e-8 → no vol information left. Skip those cases in the round trip; do not filter on the price.
- Test trap 2: in the wings, compare in PRICE space (reprices to 1e-8), not in sigma: sigma is ill-conditioned when vega → 0.
- Test trap 3: an ATM call at 1e-3 is NOT a "tiny valid price": it is below the parity lower bound. To test a small valid price, use an OTM call (K=150).

Pseudocode

    impliedVol(px, p, type, lo, hi, tol, maxIter):
        if T<=0 or S<=0 or K<=0 or lo<=0 or hi<=lo or tol<=0 or maxIter<=0 or !finite(px): InvalidInput
        lower, upper = bounds(type)
        if !(lower < px < upper): NoArbitrageViolation
        if f(lo) > 0 or f(hi) < 0: DidNotConverge
        sigma = (lo+hi)/2
        repeat maxIter:
            fs = f(sigma); if |fs| < tol: return sigma
            if fs < 0: lo = sigma else hi = sigma
            next = sigma - fs/vega(sigma)        (if vega ~ 0: force bisection)
            if next not in (lo, hi): next = (lo+hi)/2
            sigma = next
        DidNotConverge

Tests: call/put round trip on K ∈ {60..150}, T ∈ {0.02..3}, sigma ∈ {0.08, 0.2, 0.6}; C > S e^{-qT} → NoArbitrageViolation; C < intrinsic → NoArbitrageViolation; root above sigma_hi → DidNotConverge; T = 0 → InvalidInput.

## Exercise 3 — Implied forward + MarketData

### 3a. include/data/ImpliedForward.hpp, src/data/ImpliedForward.cpp (new)

Problem: the old runner uses r = q = 0, so F = S. On SPX, the implied carry r − q ≈ +3.5 %/yr: at T = 0.8, F/S ≈ 1.03 → every k is shifted by ~0.03, which biases the skew and the ATM. Furthermore, BS(r=q=0) prices are not discounted while the quotes are → systematically wrong IVs.

Quant idea: put-call parity is model-free: C(K) − P(K) = D·(F − K). For one maturity, it is a straight line in K: slope −D, intercept D·F. An OLS fit gives D and F with no rate curve and no dividend data.

Hints
- Pair call/put on the same K and the same T (|ΔT| < 1e-9), two-sided quotes only (bid > 0, ask ≥ bid).
- Keep the ~10 strikes with the smallest |C − P|: near the forward, both legs are liquid and parity is cleanest. Deep ITM legs have wide spreads.
- Sanity check: D ∈ (0.5, 1.05], otherwise nullopt. Fewer than 3 pairs → nullopt.
- Add src/data/ImpliedForward.cpp to the add_library list in CMakeLists.txt (the lib list is explicit, unlike apps/tests).
- Black-76 trick with the existing API: BSParams{S=F, r=q=−ln(D)/T} gives S e^{-qT} = F·D and K e^{-rT} = K·D → exactly Black-76. No need to touch blackscholes.

Pseudocode

    impliedForward(calls, puts, T, n=10):
        callMid[K] = mid of valid calls at T
        pairs = [(K, callMid[K] - put.mid) for valid puts at T with K in callMid]
        if |pairs| < 3: nullopt
        sort pairs by |y|, keep the first n
        OLS y = a + bK
        D = -b ; if D not in (0.5, 1.05]: nullopt
        return {T, F = a/D, D, n}

### 3b. MarketData: malformed rows counted

Problem: `catch (...) { continue; }` silently swallows any bad row, and `std::stod("12abc")` returns 12 without error. A schema change on the Python side would go unnoticed.

Hints
- Add `int skippedRows = 0;` at the END of the MarketData struct (aggregate initialization stays compatible).
- Strict parsing: `std::stod(s, &pos)` then require `pos == s.size()`.
- Missing column → throw (getline fails). isCall ∉ {0,1}, unknown exerciseType, K ≤ 0 or T ≤ 0 → throw.
- Catch ONLY invalid_argument / out_of_range (not `...`): any other error should propagate.
- Handle CRLF (`\r` at the end of the line).
- Empty file without a header → runtime_error.

### 3c. tests/test_market_data.cpp

Hints: write a fixture into `std::filesystem::temp_directory_path()`, with 3 valid rows + 3 broken ones (bad K, missing columns, isCall=2) + one CRLF row. Check counts and skippedRows == 3. Missing directory → runtime_error. For the forward: generate BS quotes with r=4.5 %, q=1.5 % AND a smile (parity must not depend on it) → F and D to 1e-8.

## Exercise 4 — apps/calibrate_spx_main.cpp

Pipeline: load → forward per T → OTM IVs → slices → common k-range → grid → interpolation → calibration → diagnostics.

Hints
- Group all filters in a `Filters` struct (|k| ≤ 0.5, spread/mid ≤ 50 %, T ≥ 0.08, ≥ 8 points, both wings, NK = 11): a single place to print and version them.
- OTM only: puts for K < F, calls for K ≥ F. ITM options have low vega relative to their price and wide spreads → noisy IVs. This also removes the duplicate (call, put) pair on the same K.
- Fail loudly if spot.txt is missing (no hardcoded spot).
- interpolateSlice: `lower_bound` with a lambda comparator on `.first`.
- CRITICAL TRAP (found during validation): `k_lo + (NK-1)·h` can land 1 ulp above `k_hi` → interpolateSlice returns −1 on the slice that defines k_hi → the optimizer receives an IV of −1. Symptom observed: one slice with rmse_wing = 0.34, rho stuck at −0.99, RMSE 7.5 vol points. Fix: `k_grid.back() = k_hi;` and check that no −1 reaches the matrix. General lesson: never let a sentinel value reach an optimizer.
- Report: forwards (F, implied r, carry), counters (malformed rows, IV failures), params, eta/eta_max with an "ON THE BOUND" flag if > 0.99, then a table per T: n_raw, k_min, k_max, rmse, rmse_atm (|k| ≤ 0.05), rmse_wing.

Pseudocode

    md = load(folder) ; if spot <= 0: fail
    for T in call expiries with T >= T_min: fwd[T] = impliedForward(...)
    for q in calls ∪ puts:
        F, D = fwd[q.T] (skip if absent)
        skip if not OTM, bid <= 0, spread/mid > max, |k| > k_max
        iv = impliedVol(mid, {S=F, K, T, r=q=-ln(D)/T}, type)
        by_T[T].push((k, iv)) or count failure
    slices = sorted by_T filtered (min_points, both wings)
    k_lo = max(min k), k_hi = min(max k) ; fail if k_lo >= k_hi
    k_grid = linspace(k_lo, k_hi, NK) ; pin both endpoints
    iv(i,j) = interp(slice_i, k_j) ; fail if <= 0
    p = calibrateSSVI(...) ; d = diagnoseSSVIFit(...)
    print everything

## 6. Reading the SPX result (2026-09-12, after the fixes)

Measured output:
- forwards: carry r − q ≈ +3.3 % to +3.9 %, implied r ≈ 4.1–5.3 %. Consistent with the market → the forward stage is sound.
- 4268 IVs, 0 inversion failures, 20 slices, k ∈ [−0.39, 0.065].
- rho = −0.54, eta = 1.654, gamma = 0.495, nu = 0.0167, eta/eta_max = 0.9999 → ON THE BOUND.
- RMSE 1.6 vol points; rmse_wing falls with T, while rmse_atm RISES with T (0.8 pt → 2.7 pt at T = 0.8).

Interpretation (the synthetic test passes, including near the bound → this is not the optimizer):
1. Rising ATM error with T = signature of θ(T) = ν·T being too rigid: the ATM term structure is not linear in T.
2. u = 1 and gamma = 0.5 (upper bound): the model wants more short-dated curvature than the global Thm 4.2 bounds allow (sufficient, not necessary conditions).
3. Common k-range [−0.39, 0.065]: the right wing is very short → rho is identified almost only by the put wing.

Phase-1 decision: the stage-1 pipeline is complete and diagnosable. The next modeling change (phase 1.5) is a free ATM θ_i per maturity (read from market IV at k=0, θ_i = σ_ATM²·T_i, enforced non-decreasing), with only (rho, eta, gamma) calibrated. Do not change the optimizer.

## Final checklist

- [ ] ctest 4/4 green
- [ ] the runner prints forwards, malformed rows, IV failures
- [ ] no −1 / NaN reaches calibrateSSVI
- [ ] eta/eta_max and ATM/wing per slice printed
- [ ] the result is written down and interpreted (section 6) in notes/quant_fit_state.md

---

# PART B — SOLUTION (full code, compiled and tested)

## B.1 CMakeLists.txt — line to add to add_library

```cmake
  src/data/MarketData.cpp
  src/data/ImpliedForward.cpp   # <- new
```

## B.2 include/data/MarketData.hpp

```cpp
#ifndef MARKETDATA_HPP
#define MARKETDATA_HPP

#include <string>
#include <vector>
#include "data/RateCurve.hpp"
#include "data/DivCurve.hpp"

namespace vse::data {

    enum class ExerciseType { European, American };

    struct OptionQuote {
        double K;
        double T;
        bool isCall;
        ExerciseType exercise;
        double bid;
        double ask;
        int volume;
        int openInterest;
        double mid() const noexcept { return 0.5 * (bid + ask); }
    };

    struct MarketData {
        double spot;
        RateCurve rates;
        DivCurve divs;
        std::vector<OptionQuote> calls;
        std::vector<OptionQuote> puts;
        int skippedRows = 0;   // malformed rows in quotes.csv (never silently lost)

        static MarketData load(const std::string& folder, const std::string& today);
    };

} // namespace vse::data

#endif
```

## B.3 src/data/MarketData.cpp

```cpp
#include "data/MarketData.hpp"
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace vse::data {

namespace {

// Strict CSV field reader: throws if the column is missing.
std::string nextField(std::istringstream& ss) {
    std::string token;
    if (!std::getline(ss, token, ','))
        throw std::invalid_argument("missing column");
    return token;
}

// std::stod accepts "12abc" (parses the prefix). Reject trailing garbage.
double toDouble(const std::string& s) {
    std::size_t pos = 0;
    const double v = std::stod(s, &pos);
    if (pos != s.size()) throw std::invalid_argument("trailing characters: " + s);
    return v;
}

int toInt(const std::string& s) {
    std::size_t pos = 0;
    const int v = std::stoi(s, &pos);
    if (pos != s.size()) throw std::invalid_argument("trailing characters: " + s);
    return v;
}

} // namespace

// Columns of quotes.csv produced by python/data/export.py:
// K, T, isCall, exerciseType, bid, ask, volume, openInterest
// Returns the number of malformed rows that were skipped.
static int loadQuotes(const std::string& path,
                      std::vector<OptionQuote>& calls,
                      std::vector<OptionQuote>& puts)
{
    std::ifstream file(path);
    if (!file.is_open())
        throw std::runtime_error("Cannot open: " + path);

    std::string line;
    if (!std::getline(file, line))
        throw std::runtime_error("Empty file (no header): " + path);

    int skipped = 0;
    while (std::getline(file, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();  // CRLF files
        if (line.empty()) continue;
        try {
            std::istringstream ss(line);
            const double K      = toDouble(nextField(ss));
            const double T      = toDouble(nextField(ss));
            const int    callI  = toInt(nextField(ss));
            const std::string ex = nextField(ss);
            const double bid    = toDouble(nextField(ss));
            const double ask    = toDouble(nextField(ss));
            const int    volume = toInt(nextField(ss));
            const int    oi     = toInt(nextField(ss));

            if (callI != 0 && callI != 1) throw std::invalid_argument("isCall not 0/1");
            if (ex != "European" && ex != "American") throw std::invalid_argument("exerciseType");
            if (!(K > 0.0) || !(T > 0.0)) throw std::invalid_argument("K or T <= 0");

            const ExerciseType exercise = (ex == "American") ? ExerciseType::American
                                                             : ExerciseType::European;
            OptionQuote q{.K=K, .T=T, .isCall=(callI == 1), .exercise=exercise,
                          .bid=bid, .ask=ask, .volume=volume, .openInterest=oi};
            (q.isCall ? calls : puts).push_back(q);
        } catch (const std::invalid_argument&) {
            ++skipped;
        } catch (const std::out_of_range&) {
            ++skipped;
        }
    }
    return skipped;
}

MarketData MarketData::load(const std::string& folder, const std::string& today)
{
    // Spot: optional, read from spot.txt if present (0.0 otherwise)
    double spot = 0.0;
    {
        std::ifstream sf(folder + "/spot.txt");
        if (sf.is_open()) sf >> spot;
    }

    // Rates: optional, flat 0% if rates.csv is absent
    RateCurve rates = [&]() -> RateCurve {
        std::ifstream rf(folder + "/rates.csv");
        if (rf.is_open()) return RateCurve::fromCSV(folder + "/rates.csv");
        return RateCurve{{0.0, 30.0}, {0.0, 0.0}};
    }();

    // Dividends: optional, none if divs.csv is absent
    DivCurve divs = [&]() -> DivCurve {
        std::ifstream df(folder + "/divs.csv");
        if (df.is_open()) return DivCurve::fromCSV(folder + "/divs.csv", today);
        return DivCurve{};
    }();

    std::vector<OptionQuote> calls, puts;
    const int skipped = loadQuotes(folder + "/quotes.csv", calls, puts);

    return MarketData{spot, std::move(rates), std::move(divs),
                      std::move(calls), std::move(puts), skipped};
}

} // namespace vse::data
```

## B.4 include/data/ImpliedForward.hpp

```cpp
#ifndef IMPLIEDFORWARD_HPP
#define IMPLIEDFORWARD_HPP

#include "data/MarketData.hpp"

#include <optional>
#include <vector>

namespace vse::data {

// Forward and discount factor implied by put-call parity on ONE maturity:
//     C(K) - P(K) = D * (F - K)
// i.e. a straight line in K with slope -D and intercept D*F.
struct ForwardEstimate {
    double T;         // maturity (years)
    double F;         // implied forward
    double D;         // implied discount factor e^{-rT}
    int    n_pairs;   // number of (call, put) pairs used in the regression
};

// - pairs a call and a put with the same strike and maturity T (|dT| < 1e-9)
// - keeps the n_pairs strikes where |C - P| is smallest (closest to the forward,
//   where both legs are liquid and parity is cleanest)
// - OLS of (C_mid - P_mid) on K  ->  D = -slope, F = intercept / D
// - returns nullopt if fewer than 3 pairs, or if D is outside (0.5, 1.05]
std::optional<ForwardEstimate> impliedForward(
    const std::vector<OptionQuote>& calls,
    const std::vector<OptionQuote>& puts,
    double T,
    int n_pairs = 10
);

} // namespace vse::data

#endif
```

## B.5 src/data/ImpliedForward.cpp

```cpp
#include "data/ImpliedForward.hpp"

#include <algorithm>
#include <cmath>
#include <map>

namespace vse::data {

std::optional<ForwardEstimate> impliedForward(
    const std::vector<OptionQuote>& calls,
    const std::vector<OptionQuote>& puts,
    double T,
    int n_pairs
) {
    constexpr double kTTol = 1e-9;

    // 1. Index call mids by strike for this maturity (two-sided quotes only).
    std::map<double, double> call_mid;
    for (const auto& c : calls) {
        if (std::abs(c.T - T) < kTTol && c.bid > 0.0 && c.ask >= c.bid)
            call_mid[c.K] = c.mid();
    }

    // 2. Build (K, C - P) pairs.
    struct Pair { double K; double y; };
    std::vector<Pair> pairs;
    for (const auto& p : puts) {
        if (std::abs(p.T - T) >= kTTol || p.bid <= 0.0 || p.ask < p.bid) continue;
        const auto it = call_mid.find(p.K);
        if (it != call_mid.end()) pairs.push_back({p.K, it->second - p.mid()});
    }
    if (pairs.size() < 3) return std::nullopt;

    // 3. Keep the n_pairs strikes closest to the forward (smallest |C - P|).
    std::sort(pairs.begin(), pairs.end(),
              [](const Pair& a, const Pair& b) { return std::abs(a.y) < std::abs(b.y); });
    if (static_cast<int>(pairs.size()) > n_pairs) pairs.resize(n_pairs);
    if (pairs.size() < 3) return std::nullopt;

    // 4. OLS  y = a + b K
    const double n = static_cast<double>(pairs.size());
    double sK = 0.0, sY = 0.0, sKK = 0.0, sKY = 0.0;
    for (const auto& pr : pairs) {
        sK += pr.K; sY += pr.y; sKK += pr.K * pr.K; sKY += pr.K * pr.y;
    }
    const double den = n * sKK - sK * sK;
    if (std::abs(den) < 1e-12) return std::nullopt;   // all strikes identical
    const double b = (n * sKY - sK * sY) / den;
    const double a = (sY - b * sK) / n;

    // 5. Back out D and F, sanity-check D.
    const double D = -b;
    if (!(D > 0.5 && D <= 1.05)) return std::nullopt;
    return ForwardEstimate{T, a / D, D, static_cast<int>(pairs.size())};
}

} // namespace vse::data
```

## B.6 src/calibration/ImpliedVol.cpp

```cpp
#include "calibration/ImpliedVol.hpp"

#include <algorithm>
#include <cmath>
#include <numbers>

namespace vse::calibration {

namespace {

double modelPrice(vse::pricing::BSParams p, OptionType type, double sigma) {
    p.sigma = sigma;
    return (type == OptionType::Call) ? vse::pricing::callPrice(p)
                                      : vse::pricing::putPrice(p);
}

double vega(vse::pricing::BSParams p, double sigma) {
    p.sigma = sigma;
    const double D1  = vse::pricing::d1(p);
    const double pdf = std::exp(-0.5 * D1 * D1) / std::sqrt(2.0 * std::numbers::pi);
    return p.S * std::exp(-p.q * p.T) * std::sqrt(p.T) * pdf;
}

} // namespace

// Safeguarded Newton (Newton-bisection):
//  - the price is strictly increasing in sigma, so f(sigma) = model - market
//    has at most one root, and [lo, hi] is a valid bracket iff f(lo) <= 0 <= f(hi);
//  - every iteration shrinks the bracket using the sign of f;
//  - a Newton step is accepted only if it stays strictly inside the bracket,
//    otherwise we bisect. Convergence is guaranteed (at least linear),
//    quadratic near the root where vega is healthy.
std::variant<IVResult, IVError> impliedVol(
    double                 marketPrice,
    vse::pricing::BSParams params,
    OptionType             type,
    double                 sigma_lo,
    double                 sigma_hi,
    double                 tol,
    int                    maxIter
) {
    // 1. Input sanity
    if (!(params.T > 0.0) || !(params.S > 0.0) || !(params.K > 0.0)
        || !(sigma_lo > 0.0) || !(sigma_hi > sigma_lo) || !(tol > 0.0)
        || maxIter <= 0 || !std::isfinite(marketPrice)) {
        return IVError::InvalidInput;
    }

    // 2. Model-free no-arbitrage bounds (European):
    //    call: max(S e^{-qT} - K e^{-rT}, 0) < C < S e^{-qT}
    //    put : max(K e^{-rT} - S e^{-qT}, 0) < P < K e^{-rT}
    //    At the bounds the implied vol is 0 or +inf.
    const double fwdS  = params.S * std::exp(-params.q * params.T);
    const double pvK   = params.K * std::exp(-params.r * params.T);
    const double lower = (type == OptionType::Call) ? std::max(fwdS - pvK, 0.0)
                                                    : std::max(pvK - fwdS, 0.0);
    const double upper = (type == OptionType::Call) ? fwdS : pvK;
    if (!(marketPrice > lower) || !(marketPrice < upper)) {
        return IVError::NoArbitrageViolation;
    }

    // 3. Bracket check
    double lo = sigma_lo, hi = sigma_hi;
    if (modelPrice(params, type, lo) - marketPrice > 0.0 ||
        modelPrice(params, type, hi) - marketPrice < 0.0) {
        return IVError::DidNotConverge;   // root outside [sigma_lo, sigma_hi]
    }

    // 4. Safeguarded Newton
    double sigma = 0.5 * (lo + hi);
    for (int i = 0; i < maxIter; ++i) {
        const double f = modelPrice(params, type, sigma) - marketPrice;
        if (std::abs(f) < tol) return IVResult{sigma, i};

        if (f < 0.0) lo = sigma; else hi = sigma;     // shrink the bracket
        if (hi - lo < 1e-14) return IVResult{sigma, i};

        const double v = vega(params, sigma);
        double next = (v > 1e-14) ? sigma - f / v : lo - 1.0;   // v ~ 0 -> force bisection
        if (!(next > lo && next < hi)) next = 0.5 * (lo + hi);
        sigma = next;
    }
    return IVError::DidNotConverge;
}

} // namespace vse::calibration
```

## B.7 tests/test_ssvi_synthetic.cpp

```cpp
// Synthetic SSVI round-trip: if the world follows the model exactly, the fitter
// must recover the parameters. If this fails, no real-data conclusion is valid.
#include "calibration/FitDiagnostics.hpp"
#include "calibration/SSVI.hpp"

#include <Eigen/Dense>

#include <cmath>
#include <iostream>
#include <string>
#include <variant>
#include <vector>

namespace {

using vse::calibration::FitDiagnostics;
using vse::calibration::SSVIParams;

int g_failures = 0;
void check(bool cond, const std::string& msg) {
    if (!cond) { ++g_failures; std::cerr << "FAIL: " << msg << "\n"; }
}

// Production-like grid: 11 k-nodes on a common range, short to long maturities.
const std::vector<double> k_grid{ -0.30, -0.24, -0.18, -0.12, -0.06, 0.0,
                                   0.05,  0.10,  0.15,  0.20,  0.25 };
const std::vector<double> T_grid{ 0.10, 0.25, 0.50, 1.00, 2.00 };

Eigen::MatrixXd synthSurface(const SSVIParams& p) {
    Eigen::MatrixXd iv(T_grid.size(), k_grid.size());
    for (std::size_t i = 0; i < T_grid.size(); ++i)
        for (std::size_t j = 0; j < k_grid.size(); ++j)
            iv(i, j) = vse::calibration::impliedVolSSVI(k_grid[j], T_grid[i], p);
    return iv;
}

void roundTrip(const std::string& name, const SSVIParams& truth, double tol_param) {
    const Eigen::MatrixXd iv = synthSurface(truth);
    const auto res = vse::calibration::calibrateSSVI(k_grid, T_grid, iv);
    check(std::holds_alternative<SSVIParams>(res), name + ": calibration failed");
    if (!std::holds_alternative<SSVIParams>(res)) return;

    const SSVIParams fit = std::get<SSVIParams>(res);
    const FitDiagnostics d = vse::calibration::diagnoseSSVIFit(k_grid, T_grid, iv, fit);

    check(std::abs(fit.rho   - truth.rho)   < tol_param, name + ": rho");
    check(std::abs(fit.eta   - truth.eta)   < tol_param, name + ": eta");
    check(std::abs(fit.gamma - truth.gamma) < tol_param, name + ": gamma");
    check(std::abs(fit.nu    - truth.nu)    < tol_param, name + ": nu");
    check(d.rmse_total < 1e-8, name + ": rmse_total = " + std::to_string(d.rmse_total));
    check(d.eta_ratio > 0.0 && d.eta_ratio < 1.0, name + ": eta_ratio in (0,1)");
    check(d.slices.size() == T_grid.size(), name + ": one summary per maturity");
}

// 1. Interior parameters, far from every bound.
void recovers_interior_parameters() {
    roundTrip("interior", SSVIParams{.rho = -0.30, .eta = 0.50, .gamma = 0.30, .nu = 0.04}, 1e-6);
}

// 2. SPX-like parameters close to the arbitrage bound (u ~ 0.95).
//    Proves the fitter is not artificially pinned to u = 1 when the truth is below it.
void recovers_near_boundary_parameters() {
    const double rho = -0.6, gamma = 0.40;
    const double a = 1.0 - 2.0 * gamma;
    const double phiSq = std::pow(a, a) / std::pow(1.0 + a, 1.0 + a);
    const double etaMax = std::min(4.0 / (1.0 + std::abs(rho)),
                                   2.0 / std::sqrt(phiSq * (1.0 + std::abs(rho))));
    const SSVIParams truth{.rho = rho, .eta = 0.95 * etaMax, .gamma = gamma, .nu = 0.03};
    check(vse::calibration::isArbitrageFree(truth), "near-boundary truth is admissible");
    roundTrip("near-boundary", truth, 1e-5);
}

// 3. Bad dimensions must be rejected, not silently fitted.
void rejects_inconsistent_dimensions() {
    const Eigen::MatrixXd iv = Eigen::MatrixXd::Constant(T_grid.size(), k_grid.size() - 1, 0.2);
    const auto res = vse::calibration::calibrateSSVI(k_grid, T_grid, iv);
    check(std::holds_alternative<vse::calibration::SSVIError>(res), "dimension mismatch rejected");
}

} // namespace

int main() {
    recovers_interior_parameters();
    recovers_near_boundary_parameters();
    rejects_inconsistent_dimensions();
    if (g_failures > 0) { std::cerr << g_failures << " check(s) failed\n"; return 1; }
    std::cout << "test_ssvi_synthetic: all checks passed\n";
    return 0;
}
```

## B.8 tests/test_implied_vol.cpp

```cpp
// Implied-vol inversion: round trips, no-arbitrage bounds, bracket failures.
#include "calibration/ImpliedVol.hpp"
#include "pricing/blackscholes.hpp"

#include <algorithm>
#include <cmath>
#include <iostream>
#include <string>
#include <variant>

namespace {

using vse::calibration::IVError;
using vse::calibration::IVResult;
using vse::calibration::OptionType;
using vse::pricing::BSParams;

int g_failures = 0;
void check(bool cond, const std::string& msg) {
    if (!cond) { ++g_failures; std::cerr << "FAIL: " << msg << "\n"; }
}

constexpr double kLo = 1e-4, kHi = 5.0, kTol = 1e-10;
constexpr int    kMaxIter = 200;

double price(const BSParams& p, OptionType t) {
    return t == OptionType::Call ? vse::pricing::callPrice(p) : vse::pricing::putPrice(p);
}

// 1-2. Round trip on a grid that includes deep OTM wings and short maturities,
//      where a pure Newton started at the bracket midpoint diverges.
void round_trip_recovers_input_sigma(OptionType type, const std::string& tag) {
    for (double K : { 60.0, 80.0, 95.0, 100.0, 105.0, 120.0, 150.0 })
        for (double T : { 0.02, 0.25, 1.0, 3.0 })
            for (double sigma : { 0.08, 0.20, 0.60 }) {
                BSParams p{100.0, K, T, 0.04, 0.015, sigma};
                const double px = price(p, type);
                // Time value = price - intrinsic. Below ~1e-8 it is lost in double
                // rounding (deep ITM, short T): no vol information left, skip.
                const double fS = p.S * std::exp(-p.q * T), pK = K * std::exp(-p.r * T);
                const double intrinsic = type == OptionType::Call ? std::max(fS - pK, 0.0)
                                                                  : std::max(pK - fS, 0.0);
                if (px - intrinsic < 1e-8) continue;
                const auto res = vse::calibration::impliedVol(px, p, type, kLo, kHi, kTol, kMaxIter);
                const std::string ctx = tag + " K=" + std::to_string(K) + " T=" + std::to_string(T)
                                      + " s=" + std::to_string(sigma);
                check(std::holds_alternative<IVResult>(res), ctx + " converged");
                if (std::holds_alternative<IVResult>(res)) {
                    // Compare in price space: in the far wings sigma is ill-conditioned.
                    BSParams q = p; q.sigma = std::get<IVResult>(res).sigma;
                    check(std::abs(price(q, type) - px) < 1e-8, ctx + " reprices");
                }
            }
}

// 3. Prices outside the model-free bounds are arbitrage, not a convergence issue.
void out_of_bounds_price_is_arbitrage_error() {
    BSParams p{100.0, 100.0, 1.0, 0.04, 0.015, 0.2};
    const double fwdS = 100.0 * std::exp(-0.015);
    const auto above = vse::calibration::impliedVol(fwdS + 1.0, p, OptionType::Call, kLo, kHi, kTol, kMaxIter);
    BSParams otm{100.0, 150.0, 1.0, 0.04, 0.015, 0.2};
    const auto below = vse::calibration::impliedVol(1e-3, otm, OptionType::Call, kLo, kHi, kTol, kMaxIter);
    BSParams itm{100.0, 50.0, 1.0, 0.04, 0.015, 0.2};
    const double intrinsic = fwdS - 50.0 * std::exp(-0.04);
    const auto under_intr = vse::calibration::impliedVol(intrinsic - 0.01, itm, OptionType::Call, kLo, kHi, kTol, kMaxIter);
    check(std::holds_alternative<IVError>(above) && std::get<IVError>(above) == IVError::NoArbitrageViolation, "C > S e^{-qT}");
    check(std::holds_alternative<IVResult>(below), "tiny but valid OTM price still inverts");
    check(std::holds_alternative<IVError>(under_intr) && std::get<IVError>(under_intr) == IVError::NoArbitrageViolation, "C < intrinsic");
}

// 4. Root outside the bracket -> DidNotConverge; bad inputs -> InvalidInput.
void bracket_and_input_errors_are_reported() {
    BSParams p{100.0, 100.0, 1.0, 0.0, 0.0, 0.8};
    const double px = vse::pricing::callPrice(p);
    const auto res = vse::calibration::impliedVol(px, p, OptionType::Call, 0.01, 0.5, kTol, kMaxIter);
    check(std::holds_alternative<IVError>(res) && std::get<IVError>(res) == IVError::DidNotConverge, "root above sigma_hi");

    BSParams bad{100.0, 100.0, 0.0, 0.0, 0.0, 0.2};
    const auto inv = vse::calibration::impliedVol(5.0, bad, OptionType::Call, kLo, kHi, kTol, kMaxIter);
    check(std::holds_alternative<IVError>(inv) && std::get<IVError>(inv) == IVError::InvalidInput, "T = 0");
}

} // namespace

int main() {
    round_trip_recovers_input_sigma(OptionType::Call, "call");
    round_trip_recovers_input_sigma(OptionType::Put,  "put");
    out_of_bounds_price_is_arbitrage_error();
    bracket_and_input_errors_are_reported();
    if (g_failures > 0) { std::cerr << g_failures << " check(s) failed\n"; return 1; }
    std::cout << "test_implied_vol: all checks passed\n";
    return 0;
}
```

## B.9 tests/test_market_data.cpp

```cpp
// Data boundary Python -> C++: parsing, malformed rows, defaults, implied forward.
#include "data/ImpliedForward.hpp"
#include "data/MarketData.hpp"
#include "pricing/blackscholes.hpp"

#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>

namespace fs = std::filesystem;

namespace {

using vse::data::ExerciseType;
using vse::data::MarketData;
using vse::data::OptionQuote;

int g_failures = 0;
void check(bool cond, const std::string& msg) {
    if (!cond) { ++g_failures; std::cerr << "FAIL: " << msg << "\n"; }
}

fs::path makeFixtureDir(const std::string& name) {
    const fs::path dir = fs::temp_directory_path() / ("vse_test_" + name);
    fs::remove_all(dir);
    fs::create_directories(dir);
    return dir;
}

// 1. Valid rows are parsed and split into calls / puts.
// 2. Malformed rows are counted in skippedRows, never silently lost.
void load_reads_fixture_and_counts_malformed_rows() {
    const fs::path dir = makeFixtureDir("quotes");
    std::ofstream(dir / "quotes.csv")
        << "K,T,isCall,exerciseType,bid,ask,volume,openInterest\n"
        << "100.0,0.5,1,European,5.0,5.2,10,100\n"
        << "100.0,0.5,0,European,4.0,4.2,12,90\n"
        << "110.0,0.5,1,American,1.0,1.1,3,40\r\n"   // CRLF line
        << "abc,0.5,1,European,1.0,1.1,3,40\n"       // bad K
        << "120.0,0.5,1,European,1.0\n"              // missing columns
        << "130.0,0.5,2,European,1.0,1.1,3,40\n"     // isCall not 0/1
        << "\n";
    std::ofstream(dir / "spot.txt") << "101.5";

    const MarketData md = MarketData::load(dir.string(), "2026-09-12");
    check(md.calls.size() == 2, "2 calls parsed");
    check(md.puts.size() == 1,  "1 put parsed");
    check(md.skippedRows == 3,  "3 malformed rows counted, got " + std::to_string(md.skippedRows));
    check(md.calls.size() == 2 && md.calls[1].exercise == ExerciseType::American, "exercise type parsed");
    check(std::abs(md.spot - 101.5) < 1e-12, "spot read from spot.txt");
    fs::remove_all(dir);
}

// 3. Missing optional files fall back to documented defaults; missing quotes throws.
void missing_files_use_documented_defaults() {
    const fs::path dir = makeFixtureDir("defaults");
    std::ofstream(dir / "quotes.csv") << "K,T,isCall,exerciseType,bid,ask,volume,openInterest\n";
    const MarketData md = MarketData::load(dir.string(), "2026-09-12");
    check(md.spot == 0.0, "spot defaults to 0 (caller must handle)");
    check(std::abs(md.rates.rate(1.0)) < 1e-15, "flat 0% rate");
    check(md.divs.divs.empty(), "no dividends");

    bool threw = false;
    try { (void)MarketData::load((dir / "nope").string(), "2026-09-12"); }
    catch (const std::runtime_error&) { threw = true; }
    check(threw, "missing quotes.csv throws");
    fs::remove_all(dir);
}

// 4. Put-call parity recovers F and D from synthetic BS quotes with r != q.
void implied_forward_recovers_carry() {
    const double S = 100.0, T = 0.75, r = 0.045, q = 0.015;
    std::vector<OptionQuote> calls, puts;
    for (double K = 70.0; K <= 130.0; K += 5.0) {
        // Smile in the data must not matter: parity is model-free.
        const double sigma = 0.2 + 0.3 * std::pow(std::log(K / S), 2);
        const vse::pricing::BSParams p{S, K, T, r, q, sigma};
        const double c = vse::pricing::callPrice(p), pu = vse::pricing::putPrice(p);
        calls.push_back({.K=K, .T=T, .isCall=true,  .exercise=ExerciseType::European,
                         .bid=c - 0.01, .ask=c + 0.01, .volume=1, .openInterest=1});
        puts.push_back ({.K=K, .T=T, .isCall=false, .exercise=ExerciseType::European,
                         .bid=pu - 0.01, .ask=pu + 0.01, .volume=1, .openInterest=1});
    }
    const auto est = vse::data::impliedForward(calls, puts, T);
    check(est.has_value(), "forward estimated");
    if (est) {
        check(std::abs(est->F - S * std::exp((r - q) * T)) < 1e-8, "F = S e^{(r-q)T}");
        check(std::abs(est->D - std::exp(-r * T)) < 1e-10, "D = e^{-rT}");
    }
    check(!vse::data::impliedForward(calls, puts, 2.0).has_value(), "no quotes at T -> nullopt");
}

} // namespace

int main() {
    load_reads_fixture_and_counts_malformed_rows();
    missing_files_use_documented_defaults();
    implied_forward_recovers_carry();
    if (g_failures > 0) { std::cerr << g_failures << " check(s) failed\n"; return 1; }
    std::cout << "test_market_data: all checks passed\n";
    return 0;
}
```

## B.10 apps/calibrate_spx_main.cpp

```cpp
// Phase-1 runner: European SSVI calibration on curated SPX quotes.
// Usage: calibrate_spx_main [folder=data/csv/SPX] [date=2026-09-12]
#include "calibration/FitDiagnostics.hpp"
#include "calibration/ImpliedVol.hpp"
#include "calibration/SSVI.hpp"
#include "data/ImpliedForward.hpp"
#include "data/MarketData.hpp"
#include "pricing/blackscholes.hpp"

#include <Eigen/Dense>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <iostream>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace {

// Phase-1 filters, in one place so they are printed and versioned together.
struct Filters {
    double k_abs_max      = 0.50;   // |log(K/F)| kept for IV extraction
    double max_rel_spread = 0.50;   // (ask-bid)/mid
    double T_min          = 0.08;   // drop the shortest expiries (pin / event risk)
    int    min_points     = 8;      // per slice
    double wing_min       = 0.05;   // slice must reach k < -wing_min and k > +wing_min
    int    NK             = 11;     // common k-grid size
};

using Slice = std::vector<std::pair<double, double>>;   // (k, iv), sorted by k

// Linear interpolation on one slice. -1 outside the covered interval.
double interpolateSlice(const Slice& pts, double k) {
    if (pts.empty() || k < pts.front().first || k > pts.back().first) return -1.0;
    const auto hi = std::lower_bound(pts.begin(), pts.end(), k,
                                     [](const auto& p, double x) { return p.first < x; });
    if (hi->first == k || hi == pts.begin()) return hi->second;
    const auto lo = std::prev(hi);
    const double w = (k - lo->first) / (hi->first - lo->first);
    return lo->second + w * (hi->second - lo->second);
}

} // namespace

int main(int argc, char** argv) {
    // STEP 1 — CLI
    const std::string folder = (argc > 1) ? argv[1] : "data/csv/SPX";
    const std::string today  = (argc > 2) ? argv[2] : "2026-09-12";
    const Filters flt;

    // STEP 2 — load
    const auto md = vse::data::MarketData::load(folder, today);
    if (md.spot <= 0.0) { std::cerr << "spot.txt missing or invalid in " << folder << "\n"; return 1; }
    std::printf("Phase 1 European SSVI calibration\n");
    std::printf("  folder=%s  date=%s  spot=%.2f\n", folder.c_str(), today.c_str(), md.spot);
    std::printf("  quotes: %zu calls, %zu puts, %d malformed rows skipped\n\n",
                md.calls.size(), md.puts.size(), md.skippedRows);

    // STEP 3 — implied forward + discount per maturity (put-call parity)
    std::set<double> expiries;
    for (const auto& q : md.calls) expiries.insert(q.T);
    std::map<double, vse::data::ForwardEstimate> fwd;
    std::printf("Implied forwards (put-call parity)\n");
    for (double T : expiries) {
        if (T < flt.T_min) continue;
        const auto est = vse::data::impliedForward(md.calls, md.puts, T);
        if (!est) continue;
        fwd[T] = *est;
        std::printf("  T=%.4f  F=%9.2f  r_impl=%+.4f  carry(r-q)=%+.4f\n",
                    T, est->F, -std::log(est->D) / T, std::log(est->F / md.spot) / T);
    }

    // STEP 4 — quotes -> IV points. OTM only: puts for K < F, calls for K >= F.
    std::map<double, Slice> by_T;
    int n_iv = 0, n_iv_fail = 0;
    auto add_iv = [&](const vse::data::OptionQuote& qt) {
        const auto it = fwd.find(qt.T);
        if (it == fwd.end()) return;
        const double F = it->second.F, D = it->second.D;
        if (qt.isCall != (qt.K >= F)) return;                     // OTM only
        const double mid = qt.mid();
        if (qt.bid <= 0.0 || mid <= 0.0 || (qt.ask - qt.bid) / mid > flt.max_rel_spread) return;
        const double k = std::log(qt.K / F);
        if (std::abs(k) > flt.k_abs_max) return;

        // Forward measure inside a spot-based API: S = F and r = q = -log(D)/T
        // gives S e^{-qT} = F D and K e^{-rT} = K D, i.e. Black-76.
        const double r = -std::log(D) / qt.T;
        const vse::pricing::BSParams bp{F, qt.K, qt.T, r, r, 0.2};
        const auto type = qt.isCall ? vse::calibration::OptionType::Call
                                    : vse::calibration::OptionType::Put;
        const auto iv = vse::calibration::impliedVol(mid, bp, type, 1e-4, 5.0, 1e-10, 200);
        if (std::holds_alternative<vse::calibration::IVResult>(iv)) {
            by_T[qt.T].push_back({k, std::get<vse::calibration::IVResult>(iv).sigma});
            ++n_iv;
        } else {
            ++n_iv_fail;
        }
    };
    for (const auto& q : md.calls) add_iv(q);
    for (const auto& q : md.puts)  add_iv(q);
    std::printf("\nIV points: %d ok, %d inversion failures\n", n_iv, n_iv_fail);

    // STEP 5 — slice filters
    std::vector<double> T_kept;
    std::vector<Slice>  slices;
    for (auto& [T, s] : by_T) {
        std::sort(s.begin(), s.end());
        if (static_cast<int>(s.size()) < flt.min_points) continue;
        if (s.front().first > -flt.wing_min || s.back().first < flt.wing_min) continue;
        T_kept.push_back(T);
        slices.push_back(s);
    }
    if (slices.empty()) { std::cerr << "No bilateral slices after filtering.\n"; return 1; }

    // STEP 6 — common k-range
    double k_lo = -1e9, k_hi = 1e9;
    for (const auto& s : slices) {
        k_lo = std::max(k_lo, s.front().first);
        k_hi = std::min(k_hi, s.back().first);
    }
    if (k_lo >= k_hi) { std::cerr << "Empty common k-range.\n"; return 1; }

    // STEP 7-8 — common grid + interpolation
    std::vector<double> k_grid(flt.NK);
    for (int j = 0; j < flt.NK; ++j)
        k_grid[j] = k_lo + j * (k_hi - k_lo) / static_cast<double>(flt.NK - 1);
    // k_lo + (NK-1)*h can land 1 ulp above k_hi -> interpolateSlice returns -1
    // on the slice that defines k_hi. Pin the endpoints exactly.
    k_grid.front() = k_lo;
    k_grid.back()  = k_hi;

    Eigen::MatrixXd iv_mkt(slices.size(), flt.NK);
    for (std::size_t i = 0; i < slices.size(); ++i)
        for (int j = 0; j < flt.NK; ++j) {
            iv_mkt(i, j) = interpolateSlice(slices[i], k_grid[j]);
            if (iv_mkt(i, j) <= 0.0) {   // never feed a sentinel to the optimiser
                std::cerr << "Interpolation hole at T=" << T_kept[i] << " k=" << k_grid[j] << "\n";
                return 1;
            }
        }

    // STEP 9 — calibration
    const auto res = vse::calibration::calibrateSSVI(k_grid, T_kept, iv_mkt);
    if (!std::holds_alternative<vse::calibration::SSVIParams>(res)) {
        std::cerr << "SSVI calibration failed (code "
                  << static_cast<int>(std::get<vse::calibration::SSVIError>(res)) << ")\n";
        return 1;
    }
    const auto p = std::get<vse::calibration::SSVIParams>(res);
    const auto d = vse::calibration::diagnoseSSVIFit(k_grid, T_kept, iv_mkt, p);

    // STEP 10 — report
    std::printf("Slices kept: %zu   common k-range: [%.4f, %.4f]   NK=%d\n\n",
                slices.size(), k_lo, k_hi, flt.NK);
    std::printf("SSVI params\n  rho=%.5f  eta=%.5f  gamma=%.5f  nu=%.5f\n", p.rho, p.eta, p.gamma, p.nu);
    std::printf("  eta/eta_max=%.4f %s\n", d.eta_ratio, d.eta_ratio > 0.99 ? "(ON THE BOUND)" : "");
    std::printf("  RMSE=%.5f  max|err|=%.5f  (vol points)\n\n", d.rmse_total, d.max_abs_error);

    // Per-slice + ATM vs wings split (|k| <= 0.05 counted as ATM)
    std::printf("  %8s %6s %10s %10s %10s %10s %10s\n",
                "T", "n_raw", "k_min", "k_max", "rmse", "rmse_atm", "rmse_wing");
    for (std::size_t i = 0; i < d.slices.size(); ++i) {
        double sa = 0, sw = 0; int na = 0, nw = 0;
        for (int j = 0; j < flt.NK; ++j) {
            const double e = vse::calibration::impliedVolSSVI(k_grid[j], T_kept[i], p) - iv_mkt(i, j);
            if (std::abs(k_grid[j]) <= 0.05) { sa += e * e; ++na; } else { sw += e * e; ++nw; }
        }
        std::printf("  %8.4f %6zu %10.4f %10.4f %10.5f %10.5f %10.5f\n",
                    d.slices[i].T, slices[i].size(), slices[i].front().first, slices[i].back().first,
                    d.slices[i].rmse, na ? std::sqrt(sa / na) : 0.0, nw ? std::sqrt(sw / nw) : 0.0);
    }
    return 0;
}
```

## B.11 Expected output (excerpt)

```
IV points: 4268 ok, 0 inversion failures
Slices kept: 20   common k-range: [-0.3899, 0.0651]   NK=11
SSVI params
  rho=-0.54024  eta=1.65432  gamma=0.49537  nu=0.01674
  eta/eta_max=0.9999 (ON THE BOUND)
  RMSE=0.01574  max|err|=0.03750  (vol points)
```
