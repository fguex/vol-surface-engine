#ifndef DIVCURVE_HPP
#define DIVCURVE_HPP

#include <vector>
#include <string>
#include "data/RateCurve.hpp"

namespace vse::data {

struct Dividend {
    double T;
    double alpha;
    double beta;
};

struct DivCurve {
    std::vector<Dividend> divs;

    static DivCurve fromCSV(const std::string& filepath, const std::string& today);
    double pvDividends(double t, double T, const RateCurve& curve) const noexcept;
    std::vector<Dividend> inInterval(double t1, double t2) const noexcept;
};

double forwardDiscrete(double S, double T, const RateCurve& rates, const DivCurve& divs) noexcept;

} // namespace vse::data

#endif