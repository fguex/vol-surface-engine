#ifndef BLACKSCHOLES_HPP
#define BLACKSCHOLES_HPP

namespace vse::pricing{

    struct BSParams {
        double S;      // Spot
        double K;      // Strike
        double T;      // Maturity (years)
        double r;      // Risk-free rate
        double q;      // Dividend yield
        double sigma;  // Volatility
    };
    double callPrice(const BSParams& p);
    double putPrice(const BSParams& p);
    double d1(const BSParams& p) noexcept;
    double d2(const BSParams& p) noexcept;

    // Forward-based pricing (accepts any forward & discount factor)
    double callPriceForward(double F, double K, double T, double df, double sigma) noexcept;
    double putPriceForward(double F, double K, double T, double df, double sigma) noexcept;
    double vegaForward(double F, double K, double T, double df, double sigma) noexcept;
};

#endif