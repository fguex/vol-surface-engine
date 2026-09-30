#ifndef FITDIAGNOSTICS_HPP
#define FITDIAGNOSTICS_HPP

#include "calibration/SSVI.hpp"

#include <Eigen/Dense>

#include <span>
#include <vector>

namespace vse::calibration {

struct SliceFitSummary {
    double T{};
    int    n_points{};
    double rmse{};
    double max_abs_error{};
};

struct FitDiagnostics {
    double rmse_total{};
    double max_abs_error{};
    double eta_ratio{};                 // eta / eta_max(gamma, rho)
    std::vector<SliceFitSummary> slices;
};

// Compute post-fit diagnostics on the exact grid used by the calibration.
//
// Expected behavior:
// - validate matrix dimensions against k_grid / T_grid
// - compute global RMSE and global max absolute error
// - compute one SliceFitSummary per maturity
// - compute eta_ratio = eta / eta_max(gamma, rho)
//
// Keep this function PURE:
// - no stdout
// - no file I/O
// - no market-data loading
FitDiagnostics diagnoseSSVIFit(
    std::span<const double> k_grid,
    std::span<const double> T_grid,
    const Eigen::MatrixXd& iv_market,
    const SSVIParams& params
);

} // namespace vse::calibration

#endif
