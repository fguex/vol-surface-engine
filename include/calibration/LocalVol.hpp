#ifndef LOCALVOL_HPP
#define LOCALVOL_HPP

#include <span>
#include <Eigen/Dense>
#include "SSVI.hpp"

namespace vse::calibration {

    double localVol(double k, double T, const SSVIParams& p) noexcept;

    Eigen::MatrixXd localVolGrid(
        std::span<const double> k_grid,
        std::span<const double> T_grid,
        const SSVIParams& p
    );

} // namespace vse::calibration

#endif