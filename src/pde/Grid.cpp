//#include <vector>
//#include <cmath>
#include "pde/Grid.hpp"

namespace vse::pde{
    Grid make_uniform_grid(double K, double x_min, double x_max, int N){
        Grid g;
        g.N = N;
        g.dx = (x_max - x_min) / N;
        g.x.resize(N+1);
        g.S.resize(N+1);
        for (int i = 0; i <= N; i ++){
            g.x[i] = x_min + i * g.dx;
            g.S[i] = K * std::exp(g.x[i]);
        }
        return g;
    }

}