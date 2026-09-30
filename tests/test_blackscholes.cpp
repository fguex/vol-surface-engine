// Purpose
// Build a tiny, deterministic test file for Black-Scholes primitives.
//
// What should live here
// - Call-put parity on a vanilla case.
// - Known reference prices for one or two parameter sets.
// - Consistency between d1/d2 and prices.
// - Edge-case policy: document what the code should do when T -> 0 or sigma -> 0.
//
// Suggested first tests
// 1. call_put_parity_holds_for_regular_inputs
// 2. analytic_prices_match_known_reference_values
// 3. forward_pricing_matches_spot_pricing_when_F_and_df_are_consistent
// 4. zero_or_near_zero_maturity_policy_is_explicit
//
// Keep this file independent from market data and calibration.

#include "pricing/blackscholes.hpp"

#include <algorithm>
#include <cmath>
#include <iostream>
#include <string>
#include <vector>

namespace {

using vse::pricing::BSParams;

// assert() is compiled out under NDEBUG (CMake Release), which would make every
// test pass silently. check() always runs and counts failures instead.
int g_failures = 0;

void check(bool cond, const std::string& msg) {
    if (!cond) {
        ++g_failures;
        std::cerr << "FAIL: " << msg << "\n";
    }
}

// Test grid: ITM / ATM / OTM, short / mid / long, low / normal / crisis vol.
// r != q on purpose, so that the forward differs from spot and sign errors on q show up.
const double S = 100.0;
const std::vector<double> K_grid     = { 80.0, 100.0, 120.0 };
const std::vector<double> T_grid     = { 0.05, 1.0, 5.0 };
const std::vector<double> sigma_grid = { 0.05, 0.2, 0.8 };
const double r = 0.03;
const double q = 0.01;

std::string describe(const BSParams& p) {
    return "S=" + std::to_string(p.S) + " K=" + std::to_string(p.K)
         + " T=" + std::to_string(p.T) + " sigma=" + std::to_string(p.sigma);
}

// 1. C - P = S e^{-qT} - K e^{-rT}. Exact identity, so near-machine tolerance.
void call_put_parity_holds_for_regular_inputs() {
    for (double K : K_grid)
        for (double T : T_grid)
            for (double sigma : sigma_grid) {
                const BSParams p{ S, K, T, r, q, sigma };
                const double lhs = vse::pricing::callPrice(p) - vse::pricing::putPrice(p);
                const double rhs = S * std::exp(-q * T) - K * std::exp(-r * T);
                check(std::abs(lhs - rhs) < 1e-10 * S, "parity: " + describe(p));
            }
}

// 2. Textbook values (Hull; Haug, "The Complete Guide to Option Pricing Formulas"),
//    published to 4 decimals, hence the 1e-4 tolerance.
void analytic_prices_match_known_reference_values() {
    const BSParams hull { 42.0, 40.0, 0.5,  0.10, 0.00, 0.20 };
    const BSParams haug { 60.0, 65.0, 0.25, 0.08, 0.00, 0.30 };
    const BSParams merton{ 100.0, 95.0, 0.5, 0.10, 0.05, 0.20 };   // q != 0

    check(std::abs(vse::pricing::callPrice(hull)  - 4.7594) < 1e-4, "Hull call");
    check(std::abs(vse::pricing::putPrice(hull)   - 0.8086) < 1e-4, "Hull put");
    check(std::abs(vse::pricing::callPrice(haug)  - 2.1334) < 1e-4, "Haug call");
    check(std::abs(vse::pricing::putPrice(merton) - 2.4648) < 1e-4, "Haug/Merton put with q");
}

// 3a. d2 = d1 - sigma sqrt(T).
void d2_is_consistent_with_d1() {
    for (double K : K_grid)
        for (double T : T_grid)
            for (double sigma : sigma_grid) {
                const BSParams p{ S, K, T, r, q, sigma };
                const double expected = vse::pricing::d1(p) - sigma * std::sqrt(T);
                check(std::abs(vse::pricing::d2(p) - expected) < 1e-12, "d1/d2: " + describe(p));
            }
}

// 3b. Spot API and forward API must agree when F = S e^{(r-q)T}, df = e^{-rT}.
void forward_pricing_matches_spot_pricing_when_F_and_df_are_consistent() {
    for (double K : K_grid)
        for (double T : T_grid)
            for (double sigma : sigma_grid) {
                const BSParams p{ S, K, T, r, q, sigma };
                const double F  = S * std::exp((r - q) * T);
                const double df = std::exp(-r * T);
                check(std::abs(vse::pricing::callPrice(p)
                               - vse::pricing::callPriceForward(F, K, T, df, sigma)) < 1e-10 * S,
                      "forward call: " + describe(p));
                check(std::abs(vse::pricing::putPrice(p)
                               - vse::pricing::putPriceForward(F, K, T, df, sigma)) < 1e-10 * S,
                      "forward put: " + describe(p));
            }
}

// 4. Model-free no-arbitrage bounds.
void prices_respect_no_arbitrage_bounds() {
    const double tol = 1e-12 * S;
    for (double K : K_grid)
        for (double T : T_grid)
            for (double sigma : sigma_grid) {
                const BSParams p{ S, K, T, r, q, sigma };
                const double C = vse::pricing::callPrice(p);
                const double P = vse::pricing::putPrice(p);
                const double Sq = S * std::exp(-q * T);
                const double Kr = K * std::exp(-r * T);
                check(C >= std::max(Sq - Kr, 0.0) - tol && C <= Sq + tol, "call bounds: " + describe(p));
                check(P >= std::max(Kr - Sq, 0.0) - tol && P <= Kr + tol, "put bounds: "  + describe(p));
            }
}

// 5. Monotonicity: both prices increase with sigma; call decreases and put increases with K.
void prices_are_monotone_in_sigma_and_strike() {
    for (double T : T_grid) {
        for (double K : K_grid)
            for (std::size_t s = 1; s < sigma_grid.size(); ++s) {
                const BSParams lo{ S, K, T, r, q, sigma_grid[s - 1] };
                const BSParams hi{ S, K, T, r, q, sigma_grid[s] };
                check(vse::pricing::callPrice(hi) > vse::pricing::callPrice(lo), "call vs sigma: " + describe(hi));
                check(vse::pricing::putPrice(hi)  > vse::pricing::putPrice(lo),  "put vs sigma: "  + describe(hi));
            }
        for (double sigma : sigma_grid)
            for (std::size_t k = 1; k < K_grid.size(); ++k) {
                const BSParams lo{ S, K_grid[k - 1], T, r, q, sigma };
                const BSParams hi{ S, K_grid[k],     T, r, q, sigma };
                check(vse::pricing::callPrice(hi) < vse::pricing::callPrice(lo), "call vs K: " + describe(hi));
                check(vse::pricing::putPrice(hi)  > vse::pricing::putPrice(lo),  "put vs K: "  + describe(hi));
            }
    }
}

// 6. Analytic vega vs central finite difference. h balances truncation O(h^2)
//    against round-off O(eps/h).
void vega_matches_finite_difference() {
    const double h = 1e-5;
    for (double K : K_grid)
        for (double T : T_grid)
            for (double sigma : sigma_grid) {
                const double F  = S * std::exp((r - q) * T);
                const double df = std::exp(-r * T);
                const double vega = vse::pricing::vegaForward(F, K, T, df, sigma);
                const double fd = (vse::pricing::callPriceForward(F, K, T, df, sigma + h)
                                 - vse::pricing::callPriceForward(F, K, T, df, sigma - h)) / (2.0 * h);
                check(std::abs(vega - fd) < 1e-6 * std::max(1.0, vega),
                      "vega FD: K=" + std::to_string(K) + " T=" + std::to_string(T)
                      + " sigma=" + std::to_string(sigma));
            }
}

// 7. Edge cases, T -> 0.
//    Away from ATM, d1 -> +/-inf and erfc handles it: the price tends to the
//    discounted intrinsic value. This is checked.
//    AT the money, d1 = log(1)/0 = 0/0 = NaN: the current code returns NaN.
//    POLICY NOT DECIDED YET (return intrinsic? throw?). Once decided, implement it
//    in blackscholes.cpp and add the corresponding check here.
void zero_or_near_zero_maturity_policy_is_explicit() {
    for (double K : { 80.0, 120.0 }) {
        const BSParams p{ S, K, 0.0, r, q, 0.2 };
        check(std::abs(vse::pricing::callPrice(p) - std::max(S - K, 0.0)) < 1e-12,
              "T=0 call intrinsic: K=" + std::to_string(K));
        check(std::abs(vse::pricing::putPrice(p) - std::max(K - S, 0.0)) < 1e-12,
              "T=0 put intrinsic: K=" + std::to_string(K));
    }
    for (double K : { 80.0, 120.0 }) {
        const BSParams p{ S, K, 1e-8, r, q, 0.2 };
        check(std::abs(vse::pricing::callPrice(p) - std::max(S - K, 0.0)) < 1e-6,
              "T->0 call converges: K=" + std::to_string(K));
    }
}

} // namespace

int main() {
    call_put_parity_holds_for_regular_inputs();
    analytic_prices_match_known_reference_values();
    d2_is_consistent_with_d1();
    forward_pricing_matches_spot_pricing_when_F_and_df_are_consistent();
    prices_respect_no_arbitrage_bounds();
    prices_are_monotone_in_sigma_and_strike();
    vega_matches_finite_difference();
    zero_or_near_zero_maturity_policy_is_explicit();

    if (g_failures > 0) {
        std::cerr << g_failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "test_blackscholes: all checks passed\n";
    return 0;
}
