#include "pde/PDEPricer.hpp"
#include "pde/Tridiag.hpp"
#include <cmath>
#include <algorithm>
#include <stdexcept>

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

    
}