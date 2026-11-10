#include "pricing/PricingKernel.hpp"

#include "pricing/blackscholes.hpp"

#include <cmath>
#include <limits>

namespace vse::model {

double LinearKernel::price(bool isCall, double F, double K, double T,
                           double df, double sigma) const {
    if (!(F > 0.0) || !(K > 0.0) || !(T > 0.0) || !(df > 0.0) || !(sigma >= 0.0))
        return std::numeric_limits<double>::quiet_NaN();

    return isCall ? vse::pricing::callPriceForward(F, K, T, df, sigma)
                  : vse::pricing::putPriceForward(F, K, T, df, sigma);
}

std::variant<vse::calibration::IVResult, vse::calibration::IVError>
LinearKernel::iv(bool isCall, double px, double F, double K, double T, double df,
                 double sigma_lo, double sigma_hi, double tol, int max_iter) const {
    using vse::calibration::IVError;
    using vse::calibration::IVResult;

    if (!(F > 0.0) || !(K > 0.0) || !(T > 0.0) || !(df > 0.0) || !std::isfinite(px))
        return IVError::InvalidInput;

    // impliedVol() est ecrit en forme « spot » : avec S = F et r = q on retrouve
    // callPrice() = df * (F N(d1) - K N(d2)), c'est-a-dire Black-76. On reutilise
    // donc le solveur existant sans le dupliquer.
    const double carry = -std::log(df) / T;
    const vse::pricing::BSParams bp{F, K, T, carry, carry, sigma_lo};
    const auto type = isCall ? vse::calibration::OptionType::Call
                             : vse::calibration::OptionType::Put;

    return vse::calibration::impliedVol(px, bp, type, sigma_lo, sigma_hi, tol, max_iter);
}

} // namespace vse::model
