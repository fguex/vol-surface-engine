#ifndef FORWARDCURVE_HPP
#define FORWARDCURVE_HPP

#include "data/DivCurve.hpp"
#include "data/RateCurve.hpp"

#include <utility>
#include <vector>

namespace vse::model {

// Le forward et le facteur d'actualisation sont les DEUX seules entrees externes dont
// le moteur a besoin pour passer d'un prix a une volatilite. Les abstraire est ce qui
// permet de remplacer « F construit par un modele » par « F lu sur les futures » sans
// toucher a SSVI, Dupire ni a la PDE.
class ForwardCurve {
public:
    virtual ~ForwardCurve() = default;

    // Prix a terme du sous-jacent pour la maturite T (annees).
    [[nodiscard]] virtual double F(double T) const = 0;

    // Facteur d'actualisation vers aujourd'hui, dans la devise de cotation (USD).
    // En numerairre coin (options inverses), vaut 1 : l'actif est sa propre unite.
    [[nodiscard]] virtual double D(double T) const = 0;
};

// Chemin historique (actions / indices) : F = (S - PV(dividendes)) / D(T).
// Sert de noyau de validation et de demonstration synthetique.
class ModelForwardCurve final : public ForwardCurve {
public:
    ModelForwardCurve(double spot,
                      vse::data::RateCurve rates,
                      vse::data::DivCurve divs);

    [[nodiscard]] double F(double T) const override;
    [[nodiscard]] double D(double T) const override;

private:
    double spot_;
    vse::data::RateCurve rates_;
    vse::data::DivCurve divs_;
};

// Chemin de production crypto : F lu sur les futures dates, interpole lineairement
// en ln F, extrapolation PLATE hors des bornes. D(T) = 1 (numerairre coin).
class MarketForwardCurve final : public ForwardCurve {
public:
    // points : (maturite en annees, forward). Trié par T croissant.
    explicit MarketForwardCurve(std::vector<std::pair<double, double>> points);

    [[nodiscard]] double F(double T) const override;
    [[nodiscard]] double D(double) const override { return 1.0; }

    [[nodiscard]] std::size_t size() const noexcept { return points_.size(); }

private:
    std::vector<std::pair<double, double>> points_;   // (T, ln F), trie
};

} // namespace vse::model

#endif
