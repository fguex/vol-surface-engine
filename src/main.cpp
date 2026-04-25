#include "pricing/blackscholes.hpp"
#include <iostream>
#include <cmath>
#include <numbers>

int main(){
    vse::pricing::BSParams p = {60, 65, 0.25, 0.08, 0.0, 0.3};
    std::cout << "valeur du call = " << vse::pricing::callPrice(p) << std::endl;
    std::cout << "--------------------------------------------------------------" << std::endl;
    std::cout << " P - C = " << vse::pricing::callPrice(p) - vse::pricing::putPrice(p) << " , Se^(-qT) - Ke^(-rt) = " << p.S*std::exp(-p.q*p.T) - p.K*std::exp(-p.r*p.T) << std::endl;
}