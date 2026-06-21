#include "pde/PDEPricer.hpp"
#include "pde/Tridiag.hpp"
#include <cmath>
#include <algorithm>
#include <stdexcept>

// TO DO : 
// Une fois le model de dividends affine ajouté corriger la méthode de pricing.
namespace vse::pde{

     // Apply border conditions and correct rhs 

     static void apply_boundary(
    OptionType type, double K, double tau,
    std::function<double(double)> r_curve,
    std::function<double(double)> q_curve,
    double T, const Grid& grid,
    const Tridiag& A,
    std::vector<double>& V,
    std::vector<double>& rhs){
        double t = T - tau;
        double df = std::exp(-r_curve(t) * tau);
        double dfq = std::exp(-q_curve(t) * tau);
        
        if (type == OptionType::Call){
            V[0] = 0.0;
            V[grid.N]  = std::max(grid.S[grid.N] * dfq - K * df, 0.0);
        } else {
            V[0]       = K * df;
            V[grid.N]  = 0.0;

        } 
        // Dirichlet correction 
        rhs[1]        -= A.lower[1]        * V[0];
        rhs[grid.N-1] -= A.upper[grid.N-1] * V[grid.N];
    }




PDEResult price(OptionType type, ExerciseStyle style,
                double S0, double K, double T,
                std::function<double(double S, double t)> sigma_loc,
                std::function<double(double t)>           r_curve,
                std::function<double(double t)>           q_curve,
                const PDEParams& p)
{
    // --- AFFINE DIV (futur) : remplacer S0 par S0* = S0 - PV(cash divs)
    //     et q_curve par q_curve + beta/T (partie proportionnelle)
    double sigma_atm = sigma_loc(S0, 0.0);
    double hw        = p.width * sigma_atm * std::sqrt(T);
    Grid   grid      = make_uniform_grid(K, -hw, +hw, p.N);
    double dt        = T / p.M;

    // Initial payoff (tau = 0)
    std::vector<double> V(p.N +1);
    for (int j = 0; j<= p.N; ++j){
        double S = grid.S[j];
        V[j] = (type == OptionType::Call)
            ? std::max(S - K, 0.0)
            : std::max(K - S, 0.0);
    }

    for (int n = 0; n < p.M; ++n){
        double tau = n * dt;

        Tridiag A, B;
        assemble_cn(A, B, grid, sigma_loc, r_curve, q_curve, tau, T, dt, p.theta_cn);

        std::vector<double> rhs(p.N + 1, 0.0);
        for (int j = 1; j < p.N; ++j)
            rhs[j] = B.lower[j]*V[j-1] + B.diag[j]*V[j] + B.upper[j]*V[j+1];

        apply_boundary(type, K, tau + dt, r_curve, q_curve, T, grid, A, V, rhs);

                std::vector<double> V_new(p.N + 1, 0.0);
        V_new[0]   = V[0];
        V_new[p.N] = V[p.N];
        thomas_solve(A, rhs, V_new);

         // --- AFFINE DIV (futur) : si un ex-date Ti ∈ [tau, tau+dt],
        //     interpoler V_new sur la grille après le saut S → S*(1-β) - α
        //     avant d'appliquer l'early exercise
        if (style == ExerciseStyle::American) {
            for (int j = 1; j < p.N; ++j) {
                double intr = (type == OptionType::Call)
                            ? std::max(grid.S[j] - K, 0.0)
                            : std::max(K - grid.S[j], 0.0);
                V_new[j] = std::max(V_new[j], intr);
            }
        }
        V = V_new;
    }

    // Interpolation en S0
    // --- AFFINE DIV (futur) : interpoler en S0* (spot clean) pas S0
    double x0    = std::log(S0 / K);
    int    j     = std::clamp((int)((x0 - grid.x[0]) / grid.dx), 0, p.N - 1);
    double alpha = (x0 - grid.x[j]) / grid.dx;
    double pv    = (1.0 - alpha)*V[j] + alpha*V[j+1];

    // Greeks
    double dS    = grid.S[j+1] - grid.S[j-1];
    double delta = (V[j+1] - V[j-1]) / dS;
    double gamma = (V[j+1] - 2*V[j] + V[j-1])
                 / ((grid.dx * grid.S[j]) * (grid.dx * grid.S[j]));

    return PDEResult{pv, delta, gamma};
}

PDEResult price(OptionType type, ExerciseStyle style,
                double S0, double K, double T,
                double sigma, double r, double q,
                const PDEParams& params)
{
    return price(type, style, S0, K, T,
        [sigma](double, double) { return sigma; },
        [r]    (double)         { return r; },
        [q]    (double)         { return q; },
        params);
}

} // namespace vse::pde