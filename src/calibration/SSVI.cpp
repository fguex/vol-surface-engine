#include <cmath>
#include <limits>
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

    // Gamma-dependent factor in  sup_th th*phi(th)^2 = eta^2 * phiSqSupFactor(gamma).
    // Internal to this translation unit -- not exposed in SSVI.hpp.
    //   for gamma in (0, 0.5]:  attained at th* = 1-2*gamma, value
    //       (1-2*gamma)^(1-2*gamma) * (2-2*gamma)^(2*gamma-2)
    //       with the limit 1 at gamma = 0.5 (the expression is 0^0 there)
    //   for gamma in (0.5, 1):  th^(1-2*gamma) diverges as th -> 0, so the sup is
    //       +inf and Thm 4.2 condition 2 can never hold. Handle it explicitly --
    //       std::pow on a negative base with a fractional exponent returns NaN,
    //       and NaN silently makes every comparison false.
    static double phiSqSupFactor(double gamma) noexcept {
        if (gamma > 0.5) return std::numeric_limits<double>::infinity();
        const double a = 1.0 - 2.0 * gamma;   // = th*, lies in [0, 1)
        if (a == 0.0) return 1.0;             // gamma == 0.5, limit of 0^0 / 1^1
        return std::pow(a, a) / std::pow(1.0 + a, 1.0 + a);
    }

    // Gatheral & Jacquier (2014), "Arbitrage-free SVI volatility surfaces".
    // Parametrisation used here: phi(th) = eta * th^(-gamma) * (1+th)^(gamma-1).
    //
    // Thm 4.1 (calendar spread) has two requirements:
    //   (a) th_t = nu*T non-decreasing in T   <=>   nu > 0
    //   (b) 0 <= d(th*phi)/dth <= phi(th) * (1 + sqrt(1-rho^2)) / rho^2
    //
    // (b) is FREE for this parametrisation and needs no code. The ratio collapses:
    //       d(th*phi)/dth / phi(th) = (1-gamma)/(1+th) <= 1-gamma < 1
    //   while the bound (1+sqrt(1-rho^2))/rho^2 >= 1 for every rho in (-1,1).
    //   So (b) holds automatically for any gamma in (0,1). Checked numerically at
    //   the SPX fit: ratio max 0.585 = 1-gamma, bound 5.897.
    //
    // Thm 4.2 (butterfly) gives TWO conditions:
    //       th*phi(th)   * (1+|rho|) <  4
    //       th*phi(th)^2 * (1+|rho|) <= 4
    //   Both suprema are closed-form for this parametrisation:
    //       sup_th th*phi   = eta                            (attained as th -> inf)
    //       sup_th th*phi^2 = eta^2 * phiSqSupFactor(gamma)  (see that function)
    //
    // These are SUFFICIENT, not necessary. The actual definition of butterfly
    // arbitrage is the Durrleman condition (G&J Lemma 2.2), on the slice total
    // variance w(k) -- positive iff the risk-neutral density is:
    //       g(k) = (1 - k*w'/(2w))^2 - (w'^2/4)*(1/w + 1/4) + w''/2  >=  0
    // Do not confuse that g(k) with phiSqSupFactor: different objects entirely.
    // Thm 4.2 buys us two scalar tests instead of a pointwise scan over k.
    //
    // How conservative: on the SPX fit (rho=-0.557, eta=2.073, gamma=0.415)
    //       cond 1 -> 3.228  passes
    //       cond 2 -> 4.126  FAILS   (eta_max = 2.041, overshoot +1.6%)
    //   yet a direct Durrleman scan over k in [-1e4, 1e4] and th in [0.002, 5]
    //   gives min g = 0.118, nowhere near zero. That surface is genuinely
    //   arbitrage-free; Thm 4.2 simply cannot certify it. Failing these tests
    //   means "not certifiable", never "arbitrage exists".
    bool isArbitrageFree(const SSVIParams& p) noexcept {
        bool c1 = std::abs(p.rho) < 1.0;

        // Thm 4.2 condition 1, global form: sup_th th*phi = eta. Strict inequality.
        bool c2 = p.eta * (1.0 + std::abs(p.rho)) < 4.0;

        // Thm 4.2 condition 2, global form -- the binding one.
        bool c3 = std::pow(p.eta, 2) * phiSqSupFactor(p.gamma)
                      * (1.0 + std::abs(p.rho)) <= 4.0;

        // Thm 4.1(a): th_t = nu*T must be increasing in T.
        bool c4 = p.nu > 0.0;

        // gamma <= 0.5 is stricter than the gamma < 1 that Thm 4.1(b) needs.
        bool c5 = p.gamma > 0.0 && p.gamma <= 0.5;

        return c1 && c2 && c3 && c4 && c5;
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
