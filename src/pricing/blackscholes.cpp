#include "pricing/blackscholes.hpp"
#include <cmath>
#include <numbers>

namespace{
    double normalCDF(double x) {
    return 0.5 * std::erfc(-x / std::numbers::sqrt2);
    }
    double normalPDF(double x){
        return 1/(std::numbers::sqrt2 * std::sqrt(std::numbers::pi)) * std::exp(-0.5*x*x);
    }
}


namespace vse::pricing{
    double d1(const BSParams& p) noexcept{
        return (std::log(p.S/p.K)+(p.r - p.q + 0.5* p.sigma * p.sigma )*p.T)/(p.sigma *std::sqrt(p.T));
    }
    double d2(const BSParams& p) noexcept{
        const double D1 = d1(p);
        return D1 - p.sigma * std::sqrt(p.T);
    }
    double callPrice(const BSParams& p){
        const double D1 = d1(p);
        const double D2 = D1 - p.sigma * std::sqrt(p.T);
        return p.S * std::exp(-p.q*p.T)*normalCDF(D1) - p.K * std::exp(-p.r*p.T)*normalCDF(D2);
    }
    double putPrice(const BSParams& p){
        const double D1 = d1(p);
        const double D2 = D1 - p.sigma*std::sqrt(p.T);
        return p.K*std::exp(-p.r * p.T) * normalCDF(-D2) - p.S * std::exp(-p.q*p.T)*normalCDF(-D1);

    }

    double callPriceForward(double F, double K, double T, double df, double sigma) noexcept {
        double d1 = (std::log(F / K) + 0.5 * sigma * sigma * T) / (sigma * std::sqrt(T));
        double d2 = d1 - sigma * std::sqrt(T);
        return df * (F * normalCDF(d1) - K * normalCDF(d2));
    }

    double putPriceForward(double F, double K, double T, double df, double sigma) noexcept {
        double d1 = (std::log(F / K) + 0.5 * sigma * sigma * T) / (sigma * std::sqrt(T));
        double d2 = d1 - sigma * std::sqrt(T);
        return df * (K * normalCDF(-d2) - F * normalCDF(-d1));
    }

    double vegaForward(double F, double K, double T, double df, double sigma) noexcept {
        double d1 = (std::log(F / K) + 0.5 * sigma * sigma * T) / (sigma * std::sqrt(T));
        return df * F * std::sqrt(T) * normalPDF(d1);
    }
}