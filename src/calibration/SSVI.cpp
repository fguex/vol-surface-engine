#include <cmath>
#include "calibration/SSVI.hpp"

namespace vse::calibration {

    double theta(double T, const SSVIParams& p) noexcept {
        return p.nu * T;
    }

    double phi(double th, const SSVIParams& p) noexcept {
        return p.eta / (std::pow(th, p.gamma) * std::pow(1.0 + th, 1.0 - p.gamma));
    }

    double totalVariance(double k, double T, const SSVIParams& p) noexcept {
        double th = theta(T, p);
        double ph = phi(th, p);
        return th / 2.0 * (1.0 + p.rho * ph * k
            + std::sqrt(std::pow(ph * k + p.rho, 2) + 1.0 - p.rho * p.rho));
    }

    double impliedVolSSVI(double k, double T, const SSVIParams& p) noexcept {
        return std::sqrt(totalVariance(k, T, p) / T);
    }

    double dw_dk(double k, double T, const SSVIParams& p) noexcept {
        double th = theta(T, p);
        double ph = phi(th, p);
        double disc = std::sqrt(std::pow(ph * k + p.rho, 2) + 1.0 - p.rho * p.rho);
        return th / 2.0 * (p.rho * ph + ph * (ph * k + p.rho) / disc);
    }

    double d2w_dk2(double k, double T, const SSVIParams& p) noexcept {
        double th = theta(T, p);
        double ph = phi(th, p);
        double inner = std::pow(ph * k + p.rho, 2) + 1.0 - p.rho * p.rho;
        return th / 2.0 * ph * ph * (1.0 - p.rho * p.rho) / std::pow(inner, 1.5);
    }

    double dw_dT(double k, double T, const SSVIParams& p) noexcept {
        // dw/dT = dtheta/dT * dw/dtheta  (chain rule)
        // For now: finite difference approximation
        double eps = T * 1e-6;
        return (totalVariance(k, T + eps, p) - totalVariance(k, T - eps, p)) / (2.0 * eps);
    }

    bool isArbitrageFree(const SSVIParams& p) noexcept {
        bool c1 = std::abs(p.rho) < 1.0;
        bool c2 = p.eta * (1.0 + std::abs(p.rho)) <= 4.0;
        bool c3 = p.gamma > 0.0 && p.gamma <= 0.5;
        return c1 && c2 && c3;
    }

} // namespace vse::calibration
