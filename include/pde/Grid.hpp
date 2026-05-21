#ifndef GRID_HPP
#define GRID_HPP
#include <vector>
#include <cmath>

namespace vse::pde{

    struct Grid{
        std::vector<double> x;
        std::vector<double> S;
        double dx;
        int N;
    };

Grid make_uniform_grid(double K, double x_min, double x_max, int N);

}

#endif 