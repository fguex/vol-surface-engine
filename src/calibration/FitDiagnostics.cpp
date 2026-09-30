#include "calibration/FitDiagnostics.hpp"
#include "calibration/SSVI.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace vse::calibration {
namespace {

// TODO(junior exercise)
// Reproduce the same arbitrage upper bound logic used by the SSVI fit.
// You can either:
// 1. duplicate the bound logic locally for now, or
// 2. later expose a shared public helper from SSVI.
//
// Recommended small helpers:
// - phiSqSupFactorLocal(gamma)
// - etaMaxBoundLocal(gamma, rho)

double phiSqSupFactorLocal(double gamma) noexcept {
    // PSEUDOCODE
    // if gamma > 0.5:
    //     return +infinity
    // a = 1 - 2 * gamma
    // if a == 0:
    //     return 1
    // return a^a / (1+a)^(1+a)

    if (gamma > 0.5)
        return std::numeric_limits<double>::infinity();
    const double a = 1.0 - 2.0 * gamma;

    if (a == 0.0)
        return 1.0;
    return std::pow(a, a) / std::pow(1.0 + a, 1.0 + a);
}

double etaMaxBoundLocal(double gamma, double rho) noexcept {
    // PSEUDOCODE
    // ar = 1 + abs(rho)
    // cond1_cap = 4 / ar
    // cond2_cap = 2 / sqrt(phiSqSupFactorLocal(gamma) * ar)
    // return min(cond1_cap, cond2_cap)
    
    const double ar = 1.0 + std::abs(rho);
    const double cond1_cap = 4.0 / ar;
    const double cond2_cap = 2.0 / std::sqrt(phiSqSupFactorLocal(gamma) * ar);

    return std::min(cond1_cap, cond2_cap);
}

} // namespace

FitDiagnostics diagnoseSSVIFit(
    std::span<const double> k_grid,
    std::span<const double> T_grid,
    const Eigen::MatrixXd& iv_market,
    const SSVIParams& params
) {
    // STEP 1 — validate dimensions
    // if iv_market.cols() != k_grid.size(): throw invalid_argument
    // if iv_market.rows() != T_grid.size(): throw invalid_argument
    if (static_cast<std::size_t>(iv_market.cols()) != k_grid.size())
        throw std::invalid_argument("k grid doesn't have the correct size");
    if (static_cast<std::size_t>(iv_market.rows()) != T_grid.size())
        throw std::invalid_argument("T grid doesn't have the correct size");
    if (k_grid.empty() || T_grid.empty())
        throw std::invalid_argument("empty calibration grid");

    // STEP 2 — initialize output + accumulators
    // FitDiagnostics out;
    // reserve out.slices to T_grid.size()
    // sse_total = 0
    // n_total = 0
    // out.max_abs_error = 0

    FitDiagnostics output;
    output.slices.reserve(T_grid.size());
    double sse_total = 0.0;
    std::size_t n_total = 0;
    output.max_abs_error = 0.0;

    // STEP 3 — maturity loop
    // for each maturity i:
    //     sse_slice = 0
    //     max_abs_slice = 0
    //     for each strike j:
    //         model_iv = impliedVolSSVI(k_grid[j], T_grid[i], params)
    //         err = model_iv - iv_market(i, j)
    //         abs_err = abs(err)
    //         accumulate total SSE
    //         accumulate slice SSE
    //         update out.max_abs_error
    //         update max_abs_slice
    //         increment n_total
    //     push one SliceFitSummary{
    //         .T = T_grid[i],
    //         .n_points = k_grid.size(),
    //         .rmse = sqrt(sse_slice / k_grid.size()),
    //         .max_abs_error = max_abs_slice
    //     }
    for (std::size_t i = 0; i < T_grid.size(); ++i) {
        double sse_slice = 0.0;
        double max_abs_slice = 0.0;
        for (std::size_t j = 0; j < k_grid.size(); ++j) {
            const double model_iv = impliedVolSSVI(k_grid[j], T_grid[i], params);
            const double err = model_iv - iv_market(i, j);
            const double abs_err = std::abs(err);

            sse_total += err * err;
            sse_slice += err * err;
            output.max_abs_error = std::max(output.max_abs_error, abs_err);
            max_abs_slice = std::max(max_abs_slice, abs_err);
            ++n_total;
        }
        output.slices.push_back(SliceFitSummary{
            .T = T_grid[i],
            .n_points = static_cast<int>(k_grid.size()),
            .rmse = std::sqrt(sse_slice / static_cast<double>(k_grid.size())),
            .max_abs_error = max_abs_slice
        });
    }
    // STEP 4 — finalize global diagnostics
    // out.rmse_total = sqrt(sse_total / n_total)
    // out.eta_ratio = params.eta / etaMaxBoundLocal(params.gamma, params.rho)
    // return out
    output.rmse_total = std::sqrt(sse_total / static_cast<double>(n_total));
    output.eta_ratio = params.eta / etaMaxBoundLocal(params.gamma, params.rho);
    return output;
}

} // namespace vse::calibration
