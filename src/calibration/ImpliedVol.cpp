#include "calibration/ImpliedVol.hpp"

#include <algorithm>
#include <cmath>
#include <numbers>

namespace {

double vega(const vse::pricing::BSParams& p) {
    const double x  = vse::pricing::d1(p);
    const double pdf = std::exp(-0.5 * x * x) / std::sqrt(2.0 * std::numbers::pi);
    return p.S * std::exp(-p.q * p.T) * std::sqrt(p.T) * pdf;
}

} // namespace

namespace vse::calibration {

// Newton garde dans un bracket : on ne converge que si l'on est sur d'encadrer la
// racine. Un Newton nu partait de (lo + hi) / 2 = 2.5 et echouait sur toute option
// de volatilite normale (vega ~ 0), d'ou un rejet massif des points de marche.
std::variant<IVResult, IVError> impliedVol(
    double                 marketPrice,
    vse::pricing::BSParams params,
    OptionType             type,
    double                 sigma_lo,
    double                 sigma_hi,
    double                 tol,
    int                    maxIter
){
    if (!(params.S > 0.0) || !(params.K > 0.0) || !(params.T > 0.0)
        || !(sigma_lo > 0.0) || !(sigma_hi > sigma_lo) || !(tol > 0.0)
        || maxIter <= 0 || !std::isfinite(marketPrice))
        return IVError::InvalidInput;

    const double disc = std::exp(-params.r * params.T);   // actualisation
    const double fwd  = params.S * std::exp(-params.q * params.T); // forward

    // Bornes de non-arbitrage du prix : hors de cet intervalle, aucune volatilite
    // implicite n'existe, et rendre une valeur serait pire que rendre une erreur.
    double lower = 0.0, upper = 0.0;
    if (type == OptionType::Call) {
        lower = std::max(fwd - params.K * disc, 0.0);
        upper = fwd;
    } else {
        lower = std::max(params.K * disc - fwd, 0.0);
        upper = params.K * disc;
    }
    const double eps = 1e-12 * std::max(1.0, upper);
    if (!(marketPrice > lower + eps) || !(marketPrice < upper - eps))
        return IVError::NoArbitrageViolation;

    auto f = [&](double sigma) {
        params.sigma = sigma;
        const double model = (type == OptionType::Call)
            ? vse::pricing::callPrice(params)
            : vse::pricing::putPrice(params);
        return model - marketPrice;
    };

    double a = sigma_lo, b = sigma_hi;
    double fa = f(a), fb = f(b);
    if (!(fa < 0.0) || !(fb > 0.0))
        return IVError::DidNotConverge;   // la racine est hors du bracket demande

    // Point de depart : approximation de Brenner-Subrahmanyam, clampee dans le bracket.
    double sigma = std::clamp(std::sqrt(2.0 * std::numbers::pi / params.T) * marketPrice / fwd,
                              a, b);

    for (int i = 0; i < maxIter; ++i) {
        const double fs  = f(sigma);
        if (std::abs(fs) <= tol) return IVResult{sigma, i};

        if (fs > 0.0) b = sigma; else a = sigma;

        const double dsigma = vega(params);
        double next = sigma - fs / dsigma;          // Newton
        if (!std::isfinite(next) || next <= a || next >= b)
            next = 0.5 * (a + b);                   // sinon, bissection

        if (std::abs(next - sigma) <= 1e-14 * std::max(1.0, sigma))
            return IVResult{next, i + 1};
        sigma = next;
    }

    return std::abs(f(sigma)) <= 1e3 * tol ? std::variant<IVResult, IVError>(IVResult{sigma, maxIter})
                                           : std::variant<IVResult, IVError>(IVError::DidNotConverge);
}

} // namespace vse::calibration
