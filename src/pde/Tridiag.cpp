#include "pde/Grid.hpp"
#include "pde/Tridiag.hpp"

namespace vse::pde{
   void assemble_cn(
    Tridiag& A, Tridiag& B,
    const Grid& grid,
    std::function<double(double S, double t)> sigma_loc,
    std::function<double(double t)> r_curve,
    std::function<double(double t)> q_curve,
    double tau,
    double T,
    double dt,
    double theta)
{
    const int    N  = grid.N;
    const double dx = grid.dx;

    A.lower.assign(N + 1, 0.0);  A.diag.assign(N + 1, 0.0);  A.upper.assign(N + 1, 0.0);
    B.lower.assign(N + 1, 0.0);  B.diag.assign(N + 1, 0.0);  B.upper.assign(N + 1, 0.0);

    const double t = T - tau;
    const double r = r_curve(t);
    const double q = q_curve(t);

    for (int j = 1; j < N; ++j) {
        double sigma = sigma_loc(grid.S[j], t);
        if (std::isnan(sigma) || sigma < 1e-4) sigma = 1e-4;

        const double sig2  = sigma * sigma;
        const double c     = r - q - 0.5 * sig2;
        const double alpha = 0.5 * sig2 / (dx*dx) - c / (2.0*dx);
        const double beta  =      -sig2 / (dx*dx) - r;
        const double gamma = 0.5 * sig2 / (dx*dx) + c / (2.0*dx);

        A.lower[j] = -theta * alpha;
        A.diag [j] =  1.0/dt - theta * beta;
        A.upper[j] = -theta * gamma;

        B.lower[j] =  (1.0 - theta) * alpha;
        B.diag [j] =  1.0/dt + (1.0 - theta) * beta;
        B.upper[j] =  (1.0 - theta) * gamma;
    }
}
void thomas_solve(const Tridiag& A,
                  std::span<const double> rhs,
                  std::span<double> out)
{
    // Solves A * out = rhs for interior nodes j = 1..N-1
    // Convention: local index i = j - 1, so j = i + 1
    // i runs from 0 to N-2 (size = N-1)
    // A.lower[j] * out[j-1] + A.diag[j] * out[j] + A.upper[j] * out[j+1] = rhs[j]
    // Boundary values out[0] and out[N] are assumed already set (Dirichlet)

    const int N = static_cast<int>(A.diag.size()) - 1;  // N+1 nodes total → N

    // Local copies of diagonal and rhs over interior nodes
    std::vector<double> d(N - 1);  // d[i] = modified diagonal at j = i+1
    std::vector<double> r(N - 1);  // r[i] = modified rhs     at j = i+1

    for (int i = 0; i < N - 1; ++i) {
        d[i] = A.diag[i + 1];
        r[i] = rhs   [i + 1];
    }

    // Forward sweep: eliminate lower diagonal
    for (int i = 1; i < N - 1; ++i) {
        double w = A.lower[i + 1] / d[i - 1];  // w = lower[j] / d[i-1]
        d[i] -= w * A.upper[i];                 // upper[j-1] = A.upper[i]
        r[i] -= w * r[i - 1];
    }

    // Back substitution
    out[N - 1] = r[N - 2] / d[N - 2];
    for (int i = N - 3; i >= 0; --i)
        out[i + 1] = (r[i] - A.upper[i + 1] * out[i + 2]) / d[i];
}
    
}