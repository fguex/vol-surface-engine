#include "calibration/ImpliedVol.hpp"
#include <cmath>
#include <functional>
#include <numbers>

namespace {

double vega(const vse::pricing::BSParams& p) {
    double D1  = vse::pricing::d1(p);
    double pdf = std::exp(-0.5 * D1 * D1) / std::sqrt(2.0 * std::numbers::pi);
    return p.S * std::exp(-p.q * p.T) * std::sqrt(p.T) * pdf;
}

std::variant<vse::calibration::IVResult, vse::calibration::IVError> Newton(
    std::function<double(double)>  f,
    std::function<double(double)>  df,
    double                         sigma_0,
    double                         tol,
    int                            maxIter
){
    double sigma = sigma_0;

    for (int i = 0; i < maxIter; ++i) {
        double fs = f(sigma);

        if (std::abs(fs) < tol)
            return vse::calibration::IVResult{sigma, i};

        double dfs = df(sigma);

        if (std::abs(dfs) < 1e-10)
            return vse::calibration::IVError::DidNotConverge;

        sigma = sigma - fs / dfs;

        if (sigma <= 0.0)
            return vse::calibration::IVError::DidNotConverge;
    }

    return vse::calibration::IVError::DidNotConverge;
}

} // namespace

namespace vse::calibration{



std::variant<IVResult, IVError> impliedVol(
    double                 marketPrice,
    vse::pricing::BSParams params,
    OptionType             type,
    double                 sigma_lo,
    double                 sigma_hi,
    double                 tol,
    int                    maxIter
){
    auto f = [&](double sigma) {
        params.sigma = sigma;
        double model = (type == OptionType::Call)
            ? vse::pricing::callPrice(params)
            : vse::pricing::putPrice(params);
        return model - marketPrice;
    };

    auto df = [&](double sigma) -> double {
        params.sigma = sigma;
        double D1  = vse::pricing::d1(params);
        double pdf = std::exp(-0.5 * D1 * D1) / std::sqrt(2.0 * std::numbers::pi);
        return params.S * std::exp(-params.q * params.T) * std::sqrt(params.T) * pdf;
    };

    double sigma_0 = 0.5 * (sigma_lo + sigma_hi);
    return Newton(f, df, sigma_0, tol, maxIter);
}

} // namespace vse::calibration
