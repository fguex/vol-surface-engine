#include <cmath>
#include <limits>
#include <Eigen/Dense>
#include "calibration/LocalVol.hpp"
#include "calibration/SSVI.hpp"

namespace vse::calibration {

    double localVol(double k, double T, const SSVIParams& p) noexcept {
        double DW_DT = dw_dT(k, T, p);
        double DW_DK = dw_dk(k, T, p);
        double D2W_DK2 = d2w_dk2(k, T, p);
        double w = totalVariance(k, T, p);
        double EPS = 1e-6;

        double denominator = 1.0 - k / w * DW_DK
            + 0.25 * (-0.25 - 1.0 / w + std::pow(k, 2) / std::pow(w, 2)) * std::pow(DW_DK, 2)
            + 0.5 * D2W_DK2;

        if (denominator <= 0.0) return std::numeric_limits<double>::quiet_NaN();
        if (DW_DT < 0.0) return std::numeric_limits<double>::quiet_NaN();
        if (w < EPS) return std::numeric_limits<double>::quiet_NaN();

        return std::sqrt(DW_DT / denominator);
    }

    Eigen::MatrixXd localVolGrid(
        std::span<const double> k_grid, std::span<const double> T_grid,
        const SSVIParams& p
    ) {
        Eigen::MatrixXd result(T_grid.size(), k_grid.size());
        for (std::size_t i = 0; i < T_grid.size(); ++i) {
            for (std::size_t j = 0; j < k_grid.size(); ++j) {
                result(i, j) = localVol(k_grid[j], T_grid[i], p);
            }
        }
        return result;
    }

} // namespace vse::calibration
