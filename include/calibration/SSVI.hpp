#ifndef SSVI_HPP
#define SSVI_HPP
#include <span>
#include <variant>
#include <Eigen/Dense>

namespace vse::calibration{

    struct SSVIParams {
        double rho;
        double eta;
        double gamma;
        double nu;
    };

    enum class SSVIError {
        ArbitrageViolation,
        DidNotConverge,
        InvalidInput
    };

    // functions
    double theta(double T, const SSVIParams& p)        noexcept;
    double phi(double theta, const SSVIParams& p)      noexcept;
    double totalVariance(double k, double T, const SSVIParams& p)  noexcept;
    double impliedVolSSVI(double k, double T, const SSVIParams& p) noexcept;
    double dw_dk(double k, double T, const SSVIParams& p)          noexcept;
    double d2w_dk2(double k, double T, const SSVIParams& p)        noexcept;
    double dw_dT(double k, double T, const SSVIParams& p)          noexcept;

    // validation
    bool isArbitrageFree(const SSVIParams& p) noexcept;

    // calibration
    std::variant<SSVIParams, SSVIError> calibrateSSVI(
        std::span<const double>  k_grid,
        std::span<const double>  T_grid,
        const Eigen::MatrixXd&   iv_market
    );

    
} 

#endif