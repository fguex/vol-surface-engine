#ifndef NELDERMEAD_HPP
#define NELDERMEAD_HPP
#include <vector>


namespace vse::opt{

    


struct NMParams{
    double alpha;
    double gamma;
    double beta;
    double delta;
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