#include "data/RateCurve.hpp"
#include <fstream>
#include <sstream>
#include <cmath>

namespace vse::data {

RateCurve::RateCurve(std::vector<double> t, std::vector<double> r)
    : tenors(std::move(t)), rates(std::move(r))
{
    if (tenors.size() != rates.size())
        throw std::invalid_argument("tenors and rates must have the same size");
    if (tenors.empty())
        throw std::invalid_argument("tenors must not be empty");
    if (!std::is_sorted(tenors.begin(), tenors.end(), std::less_equal<>()))
        throw std::invalid_argument("tenors must be strictly increasing");
}

RateCurve RateCurve::fromCSV(const std::string& filepath) {
    std::ifstream file(filepath);
    if (!file.is_open())
        throw std::runtime_error("Cannot open file: " + filepath);

    std::vector<double> tenors;
    std::vector<double> rates;
    std::string line;

    // skip header
    std::getline(file, line);

    while (std::getline(file, line)) {
        std::istringstream ss(line);
        std::string token;

        std::getline(ss, token, ',');
        double tenor = std::stod(token);

        std::getline(ss, token, ',');
        double rate = std::stod(token);

        tenors.push_back(tenor);
        rates.push_back(rate);
    }

    return RateCurve(std::move(tenors), std::move(rates));
}
double RateCurve::rate(double T) const noexcept {
    auto it = std::lower_bound(tenors.begin(), tenors.end(), T);

    if (it == tenors.begin()) return rates[0];
    if (it == tenors.end()) return rates.back();

    size_t i = std::distance(tenors.begin(), it);
    return rates[i-1] + (T - tenors[i-1]) * (rates[i] - rates[i-1]) / (tenors[i] - tenors[i-1]);
}

double RateCurve::discount(double T) const noexcept {
    return std::exp(-rate(T) * T);
}

double RateCurve::forward(double T1, double T2) const noexcept {
    return (rate(T2) * T2 - rate(T1) * T1) / (T2 - T1);
}

} 