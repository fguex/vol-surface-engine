#include "pricing/blackscholes.hpp"
#include "calibration/ImpliedVol.hpp"
#include "calibration/SSVI.hpp"
#include "calibration/LocalVol.hpp"
#include "pde/Tridiag.hpp"
#include "pde/PDEPricer.hpp"
#include "data/MarketData.hpp"
#include "data/DivCurve.hpp"
#include "pde/Grid.hpp"
#include <iostream>
#include <cmath>
#include <numbers>
#include <vector>
#include <Eigen/Dense>

int main(){
    vse::pricing::BSParams p = {.S=60, .K=65, .T=0.25, .r=0.08, .q=0.0, .sigma=0.20};
    std::cout << "valeur du call = " << vse::pricing::callPrice(p) << std::endl;
    std::cout << "--------------------------------------------------------------" << std::endl;
    std::cout << " P - C = " << vse::pricing::callPrice(p) - vse::pricing::putPrice(p) << " , Se^(-qT) - Ke^(-rt) = " << p.S*std::exp(-p.q*p.T) - p.K*std::exp(-p.r*p.T) << std::endl;

    // Now testing of Brent and the implied vol
    double price = vse::pricing::callPrice(p);
    std::cout << "Price of the call : " << price << std::endl;
    auto result = vse::calibration::impliedVol(price, p, vse::calibration::OptionType::Call, 0.01, 2.0, 1e-8, 200);

    if (std::holds_alternative<vse::calibration::IVResult>(result)) {
        auto iv = std::get<vse::calibration::IVResult>(result);
        std::cout << "Implied vol = " << iv.sigma << " (expected 0.2), iterations = " << iv.iterations << std::endl;
    } else {
        auto err = std::get<vse::calibration::IVError>(result);
        if (err == vse::calibration::IVError::DidNotConverge)
            std::cout << "Error: did not converge" << std::endl;
        else if (err == vse::calibration::IVError::NoArbitrageViolation)
            std::cout << "Error: no arbitrage violation" << std::endl;
    }

    vse::calibration::SSVIParams sp{ .rho=-0.3, .eta=0.5, .gamma=0.3, .nu=0.04 };
    std::cout << "------------------------------------------------------" << std::endl;
    std::cout << "Is it arbitrage free ? " << vse::calibration::isArbitrageFree(sp) << std::endl;
    std::cout << "Total variance : " << vse::calibration::totalVariance(0.0, 1.0, sp) << std::endl;

    double iv_left  = vse::calibration::impliedVolSSVI(-0.5, 1.0, sp);
    double iv_right = vse::calibration::impliedVolSSVI( 0.5, 1.0, sp);
    std::cout << "IV(k=-0.5) = " << iv_left << ", IV(k=0.5) = " << iv_right
          << ", diff = " << iv_left - iv_right << std::endl;

    // ---- Test calibrateSSVI ----
    std::cout << "\n====== SSVI Calibration Test ======" << std::endl;

    // True params we want to recover
    vse::calibration::SSVIParams true_params{ .rho=-0.3, .eta=0.5, .gamma=0.3, .nu=0.04 };

    // Build grids
    std::vector<double> k_vec = { -0.4, -0.2, -0.1, 0.0, 0.1, 0.2, 0.4 };
    std::vector<double> T_vec = { 0.25, 0.5, 1.0, 2.0 };

    // Generate synthetic market IV matrix (rows=T, cols=k)
    Eigen::MatrixXd iv_market(T_vec.size(), k_vec.size());
    for (size_t i = 0; i < T_vec.size(); ++i) {
        for (size_t j = 0; j < k_vec.size(); ++j) {
            iv_market(i, j) = vse::calibration::impliedVolSSVI(k_vec[j], T_vec[i], true_params);
        }
    }

    std::cout << "Synthetic IV matrix:" << std::endl << iv_market << std::endl;

    // Calibrate
    auto calib_result = vse::calibration::calibrateSSVI(k_vec, T_vec, iv_market);

    if (std::holds_alternative<vse::calibration::SSVIParams>(calib_result)) {
        auto cp = std::get<vse::calibration::SSVIParams>(calib_result);
        std::cout << "Calibration succeeded!" << std::endl;
        std::cout << "  rho   = " << cp.rho   << " (true: " << true_params.rho   << ")" << std::endl;
        std::cout << "  eta   = " << cp.eta   << " (true: " << true_params.eta   << ")" << std::endl;
        std::cout << "  gamma = " << cp.gamma << " (true: " << true_params.gamma << ")" << std::endl;
        std::cout << "  nu    = " << cp.nu    << " (true: " << true_params.nu    << ")" << std::endl;
    } else {
        auto err = std::get<vse::calibration::SSVIError>(calib_result);
        if (err == vse::calibration::SSVIError::DidNotConverge)
            std::cout << "Calibration error: did not converge" << std::endl;
        else if (err == vse::calibration::SSVIError::ArbitrageViolation)
            std::cout << "Calibration error: arbitrage violation" << std::endl;
        else
            std::cout << "Calibration error: invalid input" << std::endl;
    }

    // ---- Test Local Vol (Dupire) ----
    std::cout << "\n====== Local Vol Test ======" << std::endl;

    std::cout << "sigma_loc(0.0, 1.0) = " << vse::calibration::localVol(0.0, 1.0, true_params) << std::endl;
    std::cout << "sigma_loc(-0.3, 0.5) = " << vse::calibration::localVol(-0.3, 0.5, true_params) << std::endl;
    std::cout << "sigma_loc(0.3, 0.5) = " << vse::calibration::localVol(0.3, 0.5, true_params) << std::endl;

    std::vector<double> k_lv = { -0.3, -0.1, 0.0, 0.1, 0.3 };
    std::vector<double> T_lv = { 0.25, 0.5, 1.0, 2.0 };
    Eigen::MatrixXd lv_grid = vse::calibration::localVolGrid(k_lv, T_lv, true_params);
    std::cout << "Local vol grid:" << std::endl << lv_grid << std::endl;

    // ---- Test Market Data Ingestion ----
    std::cout << "\n====== Market Data Ingestion ======" << std::endl;

    auto mkt = vse::data::MarketData::load("data", "2026-05-16");

    std::cout << "Spot: " << mkt.spot << std::endl;
    std::cout << "Calls loaded: " << mkt.calls.size() << std::endl;
    std::cout << "Puts loaded: " << mkt.puts.size() << std::endl;
    std::cout << "Rate at 0.5y: " << mkt.rates.rate(0.5) << std::endl;
    std::cout << "Discount at 1y: " << mkt.rates.discount(1.0) << std::endl;
    std::cout << "Dividends loaded: " << mkt.divs.divs.size() << " future divs" << std::endl;

    // Forward for a few maturities
    std::cout << "\nForwards:" << std::endl;
    for (double T : {0.25, 0.5, 1.0}) {
        double F = vse::data::forwardDiscrete(mkt.spot, T, mkt.rates, mkt.divs);
        std::cout << "  F(0," << T << ") = " << F << std::endl;
    }

    // Implied vol on a few calls (treating as European approx)
    std::cout << "\nSample implied vols (first 5 calls, European approx):" << std::endl;
    int count = 0;
    for (const auto& opt : mkt.calls) {
        if (count >= 5) break;
        double F = vse::data::forwardDiscrete(mkt.spot, opt.T, mkt.rates, mkt.divs);
        double df = mkt.rates.discount(opt.T);
        double mid = opt.mid();

        // Newton inversion on callPriceForward
        double sigma = 0.3; // initial guess
        for (int iter = 0; iter < 100; ++iter) {
            double price = vse::pricing::callPriceForward(F, opt.K, opt.T, df, sigma);
            double vega = vse::pricing::vegaForward(F, opt.K, opt.T, df, sigma);
            if (std::abs(vega) < 1e-12) break;
            double diff = price - mid;
            if (std::abs(diff) < 1e-8) break;
            sigma -= diff / vega;
            if (sigma <= 0.0) { sigma = 0.01; break; }
        }

        double k = std::log(opt.K / F);
        std::cout << "  K=" << opt.K << " T=" << opt.T
                  << " mid=" << mid << " iv=" << sigma
                  << " k=" << k << std::endl;
        ++count;
    }
    const double K    = 100.0;
    const double tol  = 1e-10;

    auto g = vse::pde::make_uniform_grid(K, -1.0, 1.0, 4);

    // Taille
    assert(static_cast<int>(g.x.size()) == g.N + 1);
    assert(static_cast<int>(g.S.size()) == g.N + 1);
    assert(g.N == 4);

    // Bornes
    assert(std::abs(g.x[0] - (-1.0)) < tol);
    assert(std::abs(g.x[4] -   1.0)  < tol);

    // Pas uniforme
    assert(std::abs(g.dx - 0.5) < tol);

    // Cohérence x → S
    assert(std::abs(g.S[0] - K * std::exp(-1.0)) < tol);
    assert(std::abs(g.S[4] - K * std::exp( 1.0)) < tol);

    // ATM au nœud central
    assert(std::abs(g.x[2] - 0.0) < tol);
    assert(std::abs(g.S[2] - K)   < tol);

    // Uniformité : dx constant
    for (int j = 1; j <= g.N; ++j)
        assert(std::abs((g.x[j] - g.x[j-1]) - g.dx) < tol);

    std::cout << "Grid OK\n";
    std::cout << "  N=" << g.N << "  dx=" << g.dx << "\n";
    std::cout << "  x[0]=" << g.x[0] << "  x[N]=" << g.x[g.N] << "\n";
    std::cout << "  S[0]=" << g.S[0] << "  S[N]=" << g.S[g.N] << "\n";
    std::cout << "  S[2]=" << g.S[2] << "  (should be " << K << ")\n";

    // ── Test 1 : 3x3 system with known solution ───────────────────────────
    // | 2  -1   0 | | x1 |   | 1 |
    // |-1   2  -1 | | x2 | = | 0 |
    // | 0  -1   2 | | x3 |   | 1 |
    // Exact solution: x1 = x2 = x3 = 1
    //
    // Grid convention: N=4 (5 nodes total, j=0..4)
    // Interior nodes: j=1,2,3 → 3 interior nodes
    // Boundary nodes j=0 and j=4 are Dirichlet (not touched by Thomas)

    vse::pde::Tridiag A;
    A.lower.assign(5, 0.0);
    A.diag .assign(5, 0.0);
    A.upper.assign(5, 0.0);

    // Fill interior nodes j=1,2,3
    // j=1
    A.diag [1] =  2.0;  A.upper[1] = -1.0;
    // j=2
    A.lower[2] = -1.0;  A.diag [2] =  2.0;  A.upper[2] = -1.0;
    // j=3
    A.lower[3] = -1.0;  A.diag [3] =  2.0;

    // rhs: size N+1=5, interior values at j=1,2,3
    std::vector<double> rhs(5, 0.0);
    rhs[1] = 1.0;
    rhs[2] = 0.0;
    rhs[3] = 1.0;

    // out: boundary values already set (Dirichlet, not used by Thomas)
    std::vector<double> out(5, 0.0);

    vse::pde::thomas_solve(A, rhs, out);

    assert(std::abs(out[1] - 1.0) < tol);
    assert(std::abs(out[2] - 1.0) < tol);
    assert(std::abs(out[3] - 1.0) < tol);

    std::cout << "Test 1 (3x3 known solution): OK\n";
    std::cout << "  out[1]=" << out[1]
              << "  out[2]=" << out[2]
              << "  out[3]=" << out[3] << "\n";

    // ── Test 2 : residual check A*x - rhs = 0 ────────────────────────────
    // Reassemble a larger random-ish system and verify A*out = rhs
    const int N = 6;  // 7 nodes total, 5 interior
    vse::pde::Tridiag B;
    B.lower.assign(N + 1, 0.0);
    B.diag .assign(N + 1, 0.0);
    B.upper.assign(N + 1, 0.0);

    // Strictly diagonally dominant tridiagonal
    for (int j = 1; j < N; ++j) {
        B.lower[j] = -1.0;
        B.diag [j] =  4.0;
        B.upper[j] = -1.0;
    }

    std::vector<double> rhs2(N + 1, 0.0);
    for (int j = 1; j < N; ++j)
        rhs2[j] = static_cast<double>(j);  // arbitrary rhs

    std::vector<double> out2(N + 1, 0.0);
    vse::pde::thomas_solve(B, rhs2, out2);

    // Compute residual r = A*out2 - rhs2 on interior nodes
    double max_residual = 0.0;
    for (int j = 1; j < N; ++j) {
        double Ax = B.lower[j] * out2[j - 1]
                  + B.diag [j] * out2[j]
                  + B.upper[j] * out2[j + 1];
        max_residual = std::max(max_residual, std::abs(Ax - rhs2[j]));
    }

    assert(max_residual < tol);
    std::cout << "Test 2 (residual check, N=" << N << "): OK\n";
    std::cout << "  max residual = " << max_residual << "\n";

    // ── DivCurve affine ───────────────────────────────────────────────────
    std::cout << "\n── DivCurve affine ──\n";
    {
        vse::data::RateCurve flat({0.0, 5.0}, {0.05, 0.05});
        vse::data::DivCurve dc;
        dc.divs = {
            {0.25, 2.0, 0.0},   // cash pur dans 3 mois
            {0.75, 1.0, 0.02},  // mixte dans 9 mois
        };

        // pvDividends somme uniquement les alpha
        double pv = dc.pvDividends(0.0, 1.0, flat);
        double expected_pv = 2.0 * flat.discount(0.25) + 1.0 * flat.discount(0.75);
        std::cout << "PV dividendes = " << pv << "  (attendu " << expected_pv << ")\n";
        assert(std::abs(pv - expected_pv) < 1e-10);

        // inInterval
        auto d1 = dc.inInterval(0.0, 0.5);
        assert(d1.size() == 1 && d1[0].alpha == 2.0 && d1[0].beta == 0.0);
        std::cout << "inInterval(0, 0.5) : " << d1.size() << " div  OK\n";

        auto d2 = dc.inInterval(0.0, 1.0);
        assert(d2.size() == 2);
        std::cout << "inInterval(0, 1.0) : " << d2.size() << " divs OK\n";

        auto d3 = dc.inInterval(0.5, 1.0);
        assert(d3.size() == 1 && d3[0].alpha == 1.0 && d3[0].beta == 0.02);
        std::cout << "inInterval(0.5, 1.0) : " << d3.size() << " div  OK\n";

        std::cout << "DivCurve OK\n";
    }

    // ── PDEPricer : vol plate vs BS analytique ────────────────────────────
    std::cout << "\n── PDEPricer vs BS analytique (vol plate) ──\n";
    {
        double S0    = 100.0;
        double K     = 100.0;
        double T     = 1.0;
        double sigma = 0.20;
        double r     = 0.05;
        double q     = 0.02;

        // Référence BS analytique
        vse::pricing::BSParams bs{.S=S0, .K=K, .T=T, .r=r, .q=q, .sigma=sigma};
        double bs_call = vse::pricing::callPrice(bs);
        double bs_put  = vse::pricing::putPrice(bs);

        // PDE avec vol plate (européen)
        vse::pde::PDEParams params;
        params.N = 400;
        params.M = 200;

        auto res_call = vse::pde::price(
            vse::pde::OptionType::Call, vse::pde::ExerciseStyle::European,
            S0, K, T, sigma, r, q, params);

        auto res_put = vse::pde::price(
            vse::pde::OptionType::Put, vse::pde::ExerciseStyle::European,
            S0, K, T, sigma, r, q, params);

        std::cout << "BS  call=" << bs_call  << "  put=" << bs_put  << "\n";
        std::cout << "PDE call=" << res_call.price << "  put=" << res_put.price << "\n";
        std::cout << "err call=" << std::abs(res_call.price - bs_call)
                  << "  err put=" << std::abs(res_put.price - bs_put) << "\n";

        assert(std::abs(res_call.price - bs_call) < 0.01);
        assert(std::abs(res_put.price  - bs_put)  < 0.01);

        // Américain >= Européen
        auto res_am_put = vse::pde::price(
            vse::pde::OptionType::Put, vse::pde::ExerciseStyle::American,
            S0, K, T, sigma, r, q, params);

        assert(res_am_put.price >= res_put.price - 1e-9);
        std::cout << "Américain put=" << res_am_put.price
                  << " >= Européen put=" << res_put.price << " OK\n";

        std::cout << "PDEPricer OK\n";
    }

    return 0;

}