#include <cmath>
#include "calibration/SSVI.hpp"
#include <nlopt.hpp>

namespace vse::calibration {

    double theta(double T, const SSVIParams& p) noexcept {
        return p.nu * T;
    }

    double phi(double th, const SSVIParams& p) noexcept {
        return p.eta / (std::pow(th, p.gamma) * std::pow(1.0 + th, 1.0 - p.gamma));
    }

    double totalVariance(double k, double T, const SSVIParams& p) noexcept {
        double th = theta(T, p);
        double ph = phi(th, p);
        return th / 2.0 * (1.0 + p.rho * ph * k
            + std::sqrt(std::pow(ph * k + p.rho, 2) + 1.0 - p.rho * p.rho));
    }

    double impliedVolSSVI(double k, double T, const SSVIParams& p) noexcept {
        return std::sqrt(totalVariance(k, T, p) / T);
    }

    double dw_dk(double k, double T, const SSVIParams& p) noexcept {
        double th = theta(T, p);
        double ph = phi(th, p);
        double disc = std::sqrt(std::pow(ph * k + p.rho, 2) + 1.0 - p.rho * p.rho);
        return th / 2.0 * (p.rho * ph + ph * (ph * k + p.rho) / disc);
    }

    double d2w_dk2(double k, double T, const SSVIParams& p) noexcept {
        double th = theta(T, p);
        double ph = phi(th, p);
        double inner = std::pow(ph * k + p.rho, 2) + 1.0 - p.rho * p.rho;
        return th / 2.0 * ph * ph * (1.0 - p.rho * p.rho) / std::pow(inner, 1.5);
    }

    double dw_dT(double k, double T, const SSVIParams& p) noexcept {
        double th   = theta(T, p);
        double ph   = phi(th, p);
        double disc = std::sqrt(std::pow(ph*k + p.rho, 2) + 1.0 - p.rho*p.rho);

        double dphi_dth = -ph * (p.gamma / th + (1.0 - p.gamma) / (1.0 + th));

        double dw_dth = 0.5 * (1.0 + p.rho*ph*k + disc)
                      + th * 0.5 * (p.rho*dphi_dth*k
                          + dphi_dth*k*(ph*k + p.rho)/disc);

        return p.nu * dw_dth;
    }

    bool isArbitrageFree(const SSVIParams& p) noexcept {
        bool c1 = std::abs(p.rho) < 1.0;
        bool c2 = p.eta * (1.0 + std::abs(p.rho)) <= 4.0;
        bool c3 = p.gamma > 0.0 && p.gamma <= 0.5;
        return c1 && c2 && c3;
    }


    struct CalibData {
        std::span<const double> k_grid;
        std::span<const double> T_grid;
        const Eigen::MatrixXd&  iv_market;
    };

    static double objective(const std::vector<double>& x,
                            std::vector<double>& /*grad*/,
                            void* raw_data)
    {
        auto* d = static_cast<CalibData*>(raw_data);
        SSVIParams params{ x[0], x[1], x[2], x[3] };
        if (!isArbitrageFree(params)) return 1e6;
        double err = 0.0;
        for (size_t i = 0; i < d->T_grid.size(); ++i) {
            for (size_t j = 0; j < d->k_grid.size(); ++j) {
                double diff = impliedVolSSVI(d->k_grid[j], d->T_grid[i], params)
                            - d->iv_market(i, j);
                err += diff * diff;
            }
        }
        return err;
    }

    std::variant<SSVIParams, SSVIError> calibrateSSVI(
            std::span<const double>  k_grid,
            std::span<const double>  T_grid,
            const Eigen::MatrixXd&   iv_market
    ) {
        // Validation
        if (k_grid.size() != static_cast<size_t>(iv_market.cols())) {
            return SSVIError::InvalidInput;
        }
        if (T_grid.size() != static_cast<size_t>(iv_market.rows())) {
            return SSVIError::InvalidInput;
        }

        // Pack the data in a structure
        CalibData data{ k_grid, T_grid, iv_market };

        // Create the NLopt optimiser
        nlopt::opt opt(nlopt::LN_BOBYQA, 4); // 4 parameters, no gradient

        // Bounds
        opt.set_lower_bounds({ -0.99, 0.01, 0.01, 0.001 });
        opt.set_upper_bounds({  0.99, 5.0,  0.5,  1.0   });

        // Objective
        opt.set_min_objective(objective, &data);

        // Stopping criteria
        opt.set_xtol_rel(1e-8);
        opt.set_maxeval(10000);

        // Starting point
        std::vector<double> x = { -0.5, 1.0, 0.3, 0.04 };

        // Optimise
        try {
            double minf = 0.0;
            opt.optimize(x, minf);
        } catch (...) {
            return SSVIError::DidNotConverge;
        }

        // Check arbitrage
        SSVIParams params{ x[0], x[1], x[2], x[3] };
        if (!isArbitrageFree(params)) {
            return SSVIError::ArbitrageViolation;
        }
        return params;
    }

} 
