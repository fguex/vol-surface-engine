#ifndef PRICINGKERNEL_HPP
#define PRICINGKERNEL_HPP

#include "calibration/ImpliedVol.hpp"

#include <variant>

namespace vse::model {

// Le point de variation « convention de prix ».
//
// Tout ce qui est en amont (snapshot, SSVI, diagnostics) est agnostique. Tout ce qui
// depend du reglement (payoff en dollars ou en coin) est ICI et nulle part ailleurs.
class PricingKernel {
public:
    virtual ~PricingKernel() = default;

    // Prix d'une option europeenne, en devise de cotation (USD pour LinearUsd).
    [[nodiscard]] virtual double price(bool isCall, double F, double K, double T,
                                       double df, double sigma) const = 0;

    // Inverse d'un prix : rend la volatilite implicite, ou une erreur qui distingue
    // « prix hors bornes d'arbitrage » de « le solveur n'a pas converge ».
    [[nodiscard]] virtual std::variant<vse::calibration::IVResult,
                                       vse::calibration::IVError>
    iv(bool isCall, double px, double F, double K, double T, double df,
       double sigma_lo = 1e-4, double sigma_hi = 5.0,
       double tol = 1e-10, int max_iter = 200) const = 0;
};

// Option lineaire reglee en dollars : Black-76 avec facteur d'actualisation.
class LinearKernel final : public PricingKernel {
public:
    [[nodiscard]] double price(bool isCall, double F, double K, double T,
                               double df, double sigma) const override;

    [[nodiscard]] std::variant<vse::calibration::IVResult,
                               vse::calibration::IVError>
    iv(bool isCall, double px, double F, double K, double T, double df,
       double sigma_lo = 1e-4, double sigma_hi = 5.0,
       double tol = 1e-10, int max_iter = 200) const override;
};

} // namespace vse::model

#endif
