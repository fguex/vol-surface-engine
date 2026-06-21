#ifndef PDEPRICER_HPP
#define PDEPRICER_HPP
#include <functional>
#include "pde/Grid.hpp"

namespace vse::pde{
enum class OptionType {Call, Put};
enum class ExerciseStyle {European, American};

struct PDEParams{
    int N = 400;
    int M = 200;
    double width = 5.0;
    double theta_cn = 0.5;
};


struct PDEResult{
    double price;
    double delta;
    double gamma;
};


PDEResult price(
    OptionType type,
    ExerciseStyle style,
    double S0,
    double K,
    double T,
    std::function<double(double S, double t)> sigma_loc,
    std::function<double(double t)> r_curve,
    std::function<double(double t)> q_curve,
    const PDEParams& params = PDEParams{}
);

PDEResult price(
    OptionType type,
    ExerciseStyle style,
    double S0,
    double K,
    double T,
    double sigma,
    double r,
    double q,
    const PDEParams& params = PDEParams{}
);

} // namespace vse::pde
#endif 