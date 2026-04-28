#include "calibration/ImpliedVol.hpp"
#include <cmath>
#include <functional>

namespace {

using vse::calibration::IVError;
using vse::calibration::IVResult;

std::variant<IVResult, IVError> Brent(
    std::function<double(double)>  f,
    double                         a,
    double                         b,
    double                         tol,
    int                            maxIter
){
    double fa = f(a);
    double fb = f(b);

    if (fa * fb > 0)
        return IVError::NoArbitrageViolation;

    if (std::abs(fa) < std::abs(fb)) {
        std::swap(a, b);
        std::swap(fa, fb);
    }

    double c     = a;
    double fc    = fa;
    double d     = 0.0;
    double s;
    double fs;
    bool   mflag = true;

    for (int i = 0; i < maxIter; ++i) {
        if (std::abs(b - a) < tol)
            return IVResult{b, i};

        if (fc != fa && fb != fc) {
            s = (a * fb * fc) / ((fa - fb) * (fa - fc))
              + (b * fa * fc) / ((fb - fa) * (fb - fc))
              + (c * fa * fb) / ((fc - fa) * (fc - fb));
        } else {
            s = b - fb * (b - a) / (fb - fa);
        }

        double tmp         = (3.0 * a + b) / 4.0;
        bool not_in_bracket = !((s > tmp && s < b) || (s < tmp && s > b));
        bool cond_mflag    =  mflag && (std::abs(s - b) > 0.5 * std::abs(b - c) || std::abs(b - c) < tol);
        bool cond_no_mflag = !mflag && (std::abs(s - b) > 0.5 * std::abs(c - d) || std::abs(c - d) < tol);

        if (not_in_bracket || cond_mflag || cond_no_mflag) {
            s     = 0.5 * (a + b);
            mflag = true;
        } else {
            mflag = false;
        }

        fs = f(s);
        d  = c;
        c  = b;
        fc = fb;

        if (fa * fs < 0) {
            b  = s;
            fb = fs;
        } else {
            a  = s;
            fa = fs;
        }

        if (std::abs(fa) < std::abs(fb)) {
            std::swap(a, b);
            std::swap(fa, fb);
        }
    }

    return IVError::DidNotConverge;
}

} // namespace

namespace vse::calibration{



std::variant<IVResult, IVError> impliedVol(
    double                 marketPrice,
    vse::pricing::BSParams params,
    OptionType             type,
    double                 sigma_lo,
    double                 sigma_hi,
    double                 tol,
    int                    maxIter
){
    auto f = [&](double sigma) {
    params.sigma = sigma;
    double model = (type == OptionType::Call) 
    ? vse::pricing::callPrice(params)
    : vse::pricing::putPrice(params);
    return model - marketPrice;

    };

    auto result = Brent(f, sigma_lo, sigma_hi, tol, maxIter);

    return result;
}

} // namespace vse::calibration
