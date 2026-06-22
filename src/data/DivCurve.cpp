#include "data/DivCurve.hpp"
#include "data/DateUtils.hpp"
#include <fstream>
#include <sstream>
#include <cmath>
#include <stdexcept>

namespace vse::data {

DivCurve DivCurve::fromCSV(const std::string& filepath, const std::string& today) {
    std::ifstream file(filepath);
    if (!file.is_open())
        throw std::runtime_error("Can't open file: " + filepath);

    std::string line;
    std::getline(file, line); // skip header

    std::vector<Dividend> divs;

    while (std::getline(file, line)) {
        std::istringstream ss(line);
        std::string token;

        std::getline(ss, token, ',');
        std::string date = token;

        std::getline(ss, token, ',');
        double amount = std::stod(token);

        double T = yearsBetween(today, date);
        if (T > 0.0) {
            divs.push_back(Dividend{T, amount, 0.0});
        }
    }

    return DivCurve{std::move(divs)};
}

double DivCurve::pvDividends(double t, double T, const RateCurve& curve) const noexcept {
    double pv = 0;
    for (const auto& d: divs){
        if (d.T > t && d.T <= T){
            pv += d.alpha * curve.discount(d.T);
        }
    }
    return pv;
}

std::vector<Dividend> DivCurve::inInterval(double t1, double t2) const noexcept{
    std::vector<Dividend> result;
    for ( const auto& d : divs){
        if (d.T > t1 && d.T <= t2){
            result.push_back(d);
        }
    }
    return result;
}
double forwardDiscrete(double S, double T, const RateCurve& rates, const DivCurve& divs) noexcept {
    return (S - divs.pvDividends(0, T, rates) ) / rates.discount(T);
}

} 