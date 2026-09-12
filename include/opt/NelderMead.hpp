#ifndef NELDERMEAD_HPP
#define NELDERMEAD_HPP
#include <vector>
#include <functional>

namespace vse::opt{

    


struct NMParams{
    double alpha   = 1.0;
    double gamma   = 2.0;
    double beta    = 0.5;
    double delta   = 0.5;
    double tol_x   = 1e-6;
    double tol_f   = 1e-6;
    int    max_iter = 1000;
};

struct Vertex{
    std::vector<double> x;
    double f; 
    };

struct Simplex{
    std::vector<Vertex> vertices;
};

std::vector<double> minimize (std::function<double(const std::vector<double>&)> f, Simplex& x0, const NMParams& params);






} // end namespace

#endif 