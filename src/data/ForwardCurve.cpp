#include "data/ForwardCurve.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace vse::model {

// ---------------------------------------------------------------------------
// ModelForwardCurve : forward de modele
// ---------------------------------------------------------------------------
ModelForwardCurve::ModelForwardCurve(double spot,
                                     vse::data::RateCurve rates,
                                     vse::data::DivCurve divs)
    : spot_(spot), rates_(std::move(rates)), divs_(std::move(divs))
{
    if (!(spot_ > 0.0))
        throw std::invalid_argument("ModelForwardCurve: le spot doit etre > 0");
}

double ModelForwardCurve::F(double T) const {
    return vse::data::forwardDiscrete(spot_, T, rates_, divs_);
}

double ModelForwardCurve::D(double T) const {
    return rates_.discount(T);
}

// ---------------------------------------------------------------------------
// MarketForwardCurve : forward de marche
// ---------------------------------------------------------------------------
MarketForwardCurve::MarketForwardCurve(std::vector<std::pair<double, double>> points) {
    if (points.empty())
        throw std::invalid_argument("MarketForwardCurve: aucun point de forward");

    std::sort(points.begin(), points.end());
    for (const auto& [T, F] : points) {
        if (!(T > 0.0) || !(F > 0.0))
            throw std::invalid_argument("MarketForwardCurve: (T, F) doit etre > 0");
        if (!points_.empty() && T == points_.back().first)
            throw std::invalid_argument("MarketForwardCurve: maturite en double");
        points_.emplace_back(T, std::log(F));   // on interpole en ln F
    }
}

double MarketForwardCurve::F(double T) const {
    // Extrapolation plate hors des bornes : un forward ne s'invente pas.
    if (T <= points_.front().first) return std::exp(points_.front().second);
    if (T >= points_.back().first)  return std::exp(points_.back().second);

    const auto hi = std::lower_bound(
        points_.begin(), points_.end(), T,
        [](const std::pair<double, double>& p, double t) { return p.first < t; });

    if (hi->first == T) return std::exp(hi->second);

    const auto lo = std::prev(hi);
    const double w = (T - lo->first) / (hi->first - lo->first);
    return std::exp(lo->second + w * (hi->second - lo->second));
}

} // namespace vse::model
