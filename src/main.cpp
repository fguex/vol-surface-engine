#include "pricing/blackscholes.hpp"
#include "calibration/ImpliedVol.hpp"
#include "calibration/SSVI.hpp"
#include <iostream>
#include <cmath>
#include <numbers>

int main(){
    vse::pricing::BSParams p = {60, 65, 0.20, 0.08, 0.0, 0.001};
    std::cout << "valeur du call = " << vse::pricing::callPrice(p) << std::endl;
    std::cout << "--------------------------------------------------------------" << std::endl;
    std::cout << " P - C = " << vse::pricing::callPrice(p) - vse::pricing::putPrice(p) << " , Se^(-qT) - Ke^(-rt) = " << p.S*std::exp(-p.q*p.T) - p.K*std::exp(-p.r*p.T) << std::endl;

    // Now testing of Brent and the implied vol
    double price = vse::pricing::callPrice(p);
    std::cout << "Price of the call : " << price << std::endl;
    auto result = vse::calibration::impliedVol(price, p, vse::calibration::OptionType::Call, 1e-6, 5.0, 1e-8, 100);

    if (std::holds_alternative<vse::calibration::IVResult>(result)) {
        auto iv = std::get<vse::calibration::IVResult>(result);
        std::cout << "Implied vol = " << iv.sigma << " (expected 0.3), iterations = " << iv.iterations << std::endl;
    } else {
        auto err = std::get<vse::calibration::IVError>(result);
        if (err == vse::calibration::IVError::DidNotConverge)
            std::cout << "Error: did not converge" << std::endl;
        else if (err == vse::calibration::IVError::NoArbitrageViolation)
            std::cout << "Error: no arbitrage violation" << std::endl;
    }

    vse::calibration::SSVIParams sp{ .rho=-0.3, .eta=0.5, .gamma=0.3, .nu=0.04 };
    std::cout << "------------------------------------------------------" << std::endl;
    std::cout << "Is it arbitrage free ? " << vse::calibration::isArbitrageFree(sp) << std::endl;
    std::cout << "Total variance : " << vse::calibration::totalVariance(0.0, 1.0, sp) << std::endl;

    double iv_left  = vse::calibration::impliedVolSSVI(-0.5, 1.0, sp);
    double iv_right = vse::calibration::impliedVolSSVI( 0.5, 1.0, sp);
    std::cout << "IV(k=-0.5) = " << iv_left << ", IV(k=0.5) = " << iv_right
              << ", diff = " << iv_left - iv_right << std::endl;

    
}