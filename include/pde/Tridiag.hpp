#ifndef TRIDIAG_HPP
#define TRIDIAG_HPP
#include <vector>
#include <span>
#include <functional>
#include "pde/Grid.hpp"

namespace vse::pde{
    struct Tridiag{
        std::vector<double> lower;
        std::vector<double> diag;
        std::vector<double> upper;
    };
    
    void assemble_cn(
    Tridiag& A, Tridiag& B,
    const Grid& grid,
    std::function<double(double S, double t)> sigma_loc,
    std::function<double(double t)> r_curve,
    std::function<double(double t)> q_curve,
    double tau,   // temps à rebours courant
    double T,     // maturité — pour convertir t = T - tau
    double dt,
    double theta = 0.5);
void thomas_solve(const Tridiag& A,
                  std::span<const double> rhs,
                  std::span<double> out);
    }
#endif 