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
}