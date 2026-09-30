#include "calibration/FitDiagnostics.hpp"
#include "calibration/SSVI.hpp"

#include <Eigen/Dense>

#include <cassert>
#include <cmath>
#include <iostream>
#include <variant>
#include <vector>

int main() {
    using vse::calibration::FitDiagnostics;
    using vse::calibration::SSVIParams;

    // PURPOSE OF THIS EXERCISE
    // Prove that your calibration stack works on a world that exactly follows
    // the SSVI model. If this test fails, do not trust any conclusion drawn
    // from real market data.

    // STEP 1 — choose a known-good parameter set
    const SSVIParams true_params{
        .rho   = -0.30,
        .eta   =  0.50,
        .gamma =  0.30,
        .nu    =  0.04,
    };

    // STEP 2 — choose a realistic synthetic calibration grid
    const std::vector<double> k_grid{ -0.4, -0.2, -0.1, 0.0, 0.1, 0.2, 0.4 };
    const std::vector<double> T_grid{ 0.25, 0.50, 1.00, 2.00 };

    // STEP 3 — generate synthetic implied vols from the model itself
    Eigen::MatrixXd iv_market(T_grid.size(), k_grid.size());
    // PSEUDOCODE
    // for each maturity i:
    //     for each strike j:
    //         iv_market(i, j) = impliedVolSSVI(k_grid[j], T_grid[i], true_params)

    // STEP 4 — run the calibration
    // auto result = calibrateSSVI(k_grid, T_grid, iv_market)
    // assert that it succeeded

    // STEP 5 — extract fitted params and compute diagnostics
    // const SSVIParams fitted = ...
    // const FitDiagnostics diag = diagnoseSSVIFit(k_grid, T_grid, iv_market, fitted)

    // STEP 6 — assert parameter recovery
    // Suggested tolerances for the first clean version: 1e-6 on each parameter.
    // assert(abs(fitted.rho   - true_params.rho)   < 1e-6)
    // assert(abs(fitted.eta   - true_params.eta)   < 1e-6)
    // assert(abs(fitted.gamma - true_params.gamma) < 1e-6)
    // assert(abs(fitted.nu    - true_params.nu)    < 1e-6)

    // STEP 7 — assert fit-quality diagnostics
    // assert(diag.rmse_total is tiny)
    // assert(diag.eta_ratio > 0)
    // assert(diag.eta_ratio < 1)

    // TEMPORARY PLACEHOLDER
    // Remove this section once the real implementation is in place.
    (void)true_params;
    (void)k_grid;
    (void)T_grid;
    (void)iv_market;

    std::cout << "TODO: implement synthetic SSVI regression test\n";
    return 0;
}
