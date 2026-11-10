// Test golden du chemin general (regle en dollars), sans aucune donnee de marche :
// un monde est genere a partir de parametres SSVI connus, repasse dans la chaine
// forward -> prix -> inversion -> calibration, et doit rendre ces parametres.
//
// Il couvre exactement le seam S0 : ForwardCurve (modele), PricingKernel (lineaire),
// inversion de Black-76, puis calibrateSSVI.

#include "calibration/SSVI.hpp"
#include "data/DivCurve.hpp"
#include "data/ForwardCurve.hpp"
#include "data/RateCurve.hpp"
#include "model/Conventions.hpp"
#include "pricing/PricingKernel.hpp"

#include <Eigen/Dense>

#include <cmath>
#include <cstdio>
#include <stdexcept>
#include <variant>
#include <vector>

namespace {

int failures = 0;

void check(bool cond, const char* what) {
    if (!cond) {
        std::printf("  ECHEC : %s\n", what);
        ++failures;
    }
}

void check_close(double got, double want, double tol, const char* what) {
    const double err = std::abs(got - want);
    if (!(err <= tol)) {
        std::printf("  ECHEC : %s (got %.12g, want %.12g, err %.3g)\n", what, got, want, err);
        ++failures;
    }
}

constexpr double SPOT = 100.0;
constexpr double RATE = 0.03;

} // namespace

int main() {
    using namespace vse;
    using calibration::SSVIParams;

    const SSVIParams truth{-0.35, 0.70, 0.30, 0.045};

    std::printf("1. conventions\n");
    {
        const model::MarketConventions conv{};
        check(!conv.is_inverse(), "par defaut : reglement lineaire USD");
    }

    std::printf("2. forward de modele\n");
    model::ModelForwardCurve fwd{SPOT,
                                 data::RateCurve{{0.0, 1.0, 2.0}, {RATE, RATE, RATE}},
                                 data::DivCurve{}};
    check_close(fwd.F(1.0), SPOT * std::exp(RATE), 1e-10, "F(1) = S e^{rT} sans dividende");
    check_close(fwd.D(1.0), std::exp(-RATE), 1e-12, "D(1) = e^{-rT}");

    std::printf("3. forward de marche (futures)\n");
    {
        model::MarketForwardCurve mkt{
            std::vector<std::pair<double, double>>{{0.5, 100.0}, {1.0, 103.0}}};
        check_close(mkt.F(0.5), 100.0, 1e-12, "noeud exact a T=0.5");
        check_close(mkt.F(0.75), std::sqrt(100.0 * 103.0), 1e-10, "interpolation en ln F");
        check_close(mkt.F(2.0), 103.0, 1e-12, "extrapolation plate");
        check_close(mkt.D(1.0), 1.0, 0.0, "numerairre coin : D = 1");
    }

    std::printf("4. noyau de prix : aller-retour prix <-> volatilite\n");
    const model::LinearKernel kernel;
    {
        const double T = 1.0, K = 100.0, df = std::exp(-RATE), sigma = 0.2;
        const double px = kernel.price(true, fwd.F(T), K, T, df, sigma);
        const auto res = kernel.iv(true, px, fwd.F(T), K, T, df, 1e-4, 5.0, 1e-12);
        check(std::holds_alternative<calibration::IVResult>(res), "l'inversion converge");
        if (std::holds_alternative<calibration::IVResult>(res))
            check_close(std::get<calibration::IVResult>(res).sigma, sigma, 1e-9,
                        "sigma retrouve");

        const double put = kernel.price(false, fwd.F(T), K, T, df, sigma);
        check_close(px - put, df * (fwd.F(T) - K), 1e-10, "parite call-put");
    }

    std::printf("5. chaine complete : monde synthetique -> calibration\n");
    {
        const std::vector<double> k_grid{-0.30, -0.20, -0.15, -0.10, -0.05, 0.0,
                                          0.05,  0.10,  0.15,  0.20, 0.30};
        const std::vector<double> T_grid{0.10, 0.25, 0.50, 1.00, 2.00};

        Eigen::MatrixXd iv_mkt(static_cast<Eigen::Index>(T_grid.size()),
                               static_cast<Eigen::Index>(k_grid.size()));
        double worst_roundtrip = 0.0;

        for (std::size_t i = 0; i < T_grid.size(); ++i) {
            const double T = T_grid[i];
            const double F = fwd.F(T);
            const double df = fwd.D(T);
            for (std::size_t j = 0; j < k_grid.size(); ++j) {
                const double K = F * std::exp(k_grid[j]);
                const double sigma = calibration::impliedVolSSVI(k_grid[j], T, truth);
                const double px = kernel.price(true, F, K, T, df, sigma);

                const auto res = kernel.iv(true, px, F, K, T, df, 1e-4, 5.0, 1e-12);
                if (!std::holds_alternative<calibration::IVResult>(res)) {
                    std::printf("  ECHEC : inversion impossible k=%.2f T=%.2f\n", k_grid[j], T);
                    ++failures;
                    continue;
                }
                const double back = std::get<calibration::IVResult>(res).sigma;
                worst_roundtrip = std::max(worst_roundtrip, std::abs(back - sigma));
                iv_mkt(static_cast<Eigen::Index>(i), static_cast<Eigen::Index>(j)) = back;
            }
        }
        std::printf("  erreur d'aller-retour max : %.2e\n", worst_roundtrip);
        check(worst_roundtrip < 1e-9, "aller-retour IV a 1e-9");

        const auto fit = calibration::calibrateSSVI(k_grid, T_grid, iv_mkt);
        check(std::holds_alternative<SSVIParams>(fit), "la calibration converge");
        if (std::holds_alternative<SSVIParams>(fit)) {
            const auto& p = std::get<SSVIParams>(fit);
            std::printf("  retrouve : rho=%.8f eta=%.8f gamma=%.8f nu=%.8f\n",
                        p.rho, p.eta, p.gamma, p.nu);
            check_close(p.rho,   truth.rho,   1e-4, "rho retrouve");
            check_close(p.eta,   truth.eta,   1e-4, "eta retrouve");
            check_close(p.gamma, truth.gamma, 1e-4, "gamma retrouve");
            check_close(p.nu,    truth.nu,    1e-6, "nu retrouve");
            check(calibration::isArbitrageFree(p), "le fit est sans arbitrage");
        }
    }

    if (failures == 0) {
        std::printf("OK : chaine generale validee sans donnee de marche.\n");
        return 0;
    }
    std::printf("%d echec(s).\n", failures);
    return 1;
}
