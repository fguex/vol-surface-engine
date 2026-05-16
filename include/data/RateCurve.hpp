#ifndef RATECURVE_HPP
#define RATECURVE_HPP
#include <vector>
#include <string>
#include <stdexcept>
#include <algorithm>

namespace vse::data {

struct RateCurve {
    std::vector<double> tenors;
    std::vector<double> rates;

    RateCurve(std::vector<double> t, std::vector<double> r);

    double rate(double T) const noexcept;
    double discount(double T) const noexcept;
    double forward(double T1, double T2) const noexcept;

    static RateCurve fromCSV(const std::string& filepath);
};

} // namespace vse::data

#endif 