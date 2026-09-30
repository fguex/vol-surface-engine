#include "calibration/FitDiagnostics.hpp"
#include "calibration/ImpliedVol.hpp"
#include "calibration/SSVI.hpp"
#include "data/MarketData.hpp"
#include "pricing/blackscholes.hpp"

#include <Eigen/Dense>

#include <algorithm>
#include <cmath>
#include <iostream>
#include <limits>
#include <map>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace {

struct IVPoint {
    double k;
    double T;
    double iv;
};

// TODO(junior exercise)
// Implement a simple linear interpolation on one maturity slice.
// Contract:
// - pts is sorted by k ascending
// - return -1.0 if k lies outside the covered interval
// - otherwise linearly interpolate between the two surrounding nodes
double interpolateSlice(const std::vector<std::pair<double, double>>& pts, double k) {
    // PSEUDOCODE
    // if pts empty: return -1
    // if k < first_k or k > last_k: return -1
    // lower_bound on k
    // if exact/first point: return corresponding iv
    // lo = previous node, hi = current node
    // w = (k - lo.k) / (hi.k - lo.k)
    // return lo.iv + w * (hi.iv - lo.iv)
    (void)pts;
    (void)k;
    return -1.0;
}

} // namespace

int main(int argc, char** argv) {
    // GOAL
    // Build a clean phase-1 runner for European SSVI calibration on curated SPX data.
    //
    // DELIVERABLES OF THIS MAIN
    // - load market data
    // - compute valid implied-vol points
    // - group by maturity
    // - retain bilateral slices with enough points
    // - build a common k-grid
    // - interpolate onto that grid
    // - run calibrateSSVI
    // - print diagnostics from diagnoseSSVIFit

    // STEP 1 — parse CLI arguments
    // default folder: data/csv/SPX
    // default date  : 2026-09-12
    const std::string folder = (argc > 1) ? argv[1] : "data/csv/SPX";
    const std::string today  = (argc > 2) ? argv[2] : "2026-09-12";

    // STEP 2 — load market data and define simple rate/dividend assumptions
    // For phase 1 you may keep r = 0 and q = 0, but print the assumption clearly.
    const auto md = vse::data::MarketData::load(folder, today);
    const double S0 = md.spot > 0.0 ? md.spot : 7656.98;
    const double r = 0.0;
    const double q = 0.0;

    std::cout << "TODO: implement phase-1 SPX calibration runner\n";
    std::cout << "Loaded folder=" << folder << ", date=" << today << ", spot=" << S0 << "\n";
    std::cout << "Assumptions: r=" << r << ", q=" << q << "\n";

    // STEP 3 — quote -> implied vol points
    // Suggested local lambda add_iv(quote, is_call):
    // - mid = quote.mid(); skip if <= 0
    // - F = S0 * exp((r-q) * T)
    // - k = log(K / F)
    // - optional filter: keep only |k| <= 0.5 for phase 1
    // - invert implied vol with calibration::impliedVol(...)
    // - if success: push IVPoint{k, T, iv}
    //
    // Then loop over md.calls and md.puts.

    // STEP 4 — group by maturity
    // std::map<double, std::vector<std::pair<double,double>>> by_T;
    // for each point p:
    //     by_T[p.T].push_back({p.k, p.iv})

    // STEP 5 — filter slices
    // Keep only maturities with enough points and both wings:
    // - size >= 8
    // - T >= 0.08
    // - left wing present  (k_min < -0.05)
    // - right wing present (k_max >  0.05)
    // Also sort each slice by k.

    // STEP 6 — build common k-range
    // Intersect all retained slices:
    // k_lo = max of slice minima
    // k_hi = min of slice maxima
    // fail if k_lo >= k_hi

    // STEP 7 — build common k-grid
    // Example: NK = 11 evenly spaced nodes between k_lo and k_hi.

    // STEP 8 — interpolate each slice on the common grid
    // Keep only fully interpolable rows.
    // Build Eigen::MatrixXd iv_ok(rows, NK).

    // STEP 9 — run calibration
    // auto result = calibrateSSVI(k_grid, T_ok, iv_ok)
    // if failure: print an error and return non-zero

    // STEP 10 — diagnostics
    // params = fitted SSVIParams
    // diag = diagnoseSSVIFit(k_grid, T_ok, iv_ok, params)
    // print:
    // - number of valid IV points
    // - number of retained maturities
    // - common k-range
    // - rho / eta / gamma / nu
    // - eta_ratio
    // - total RMSE and max abs error
    // - one line per maturity slice

    // STEP 11 — stretch goal
    // Print separate summaries for ATM-ish nodes vs wing nodes if you want more insight.

    return 0;
}
