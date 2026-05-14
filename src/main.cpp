#include "pricing/blackscholes.hpp"
#include "calibration/ImpliedVol.hpp"
#include "calibration/SSVI.hpp"
#include "calibration/LocalVol.hpp"
#include <iostream>
#include <cmath>
#include <numbers>
#include <vector>
#include <Eigen/Dense>

int main(){
    vse::pricing::BSParams p = {.S=60, .K=65, .T=0.25, .r=0.08, .q=0.0, .sigma=0.20};
    std::cout << "valeur du call = " << vse::pricing::callPrice(p) << std::endl;
    std::cout << "--------------------------------------------------------------" << std::endl;
    std::cout << " P - C = " << vse::pricing::callPrice(p) - vse::pricing::putPrice(p) << " , Se^(-qT) - Ke^(-rt) = " << p.S*std::exp(-p.q*p.T) - p.K*std::exp(-p.r*p.T) << std::endl;

    // Now testing of Brent and the implied vol
    double price = vse::pricing::callPrice(p);
    std::cout << "Price of the call : " << price << std::endl;
    auto result = vse::calibration::impliedVol(price, p, vse::calibration::OptionType::Call, 0.01, 2.0, 1e-8, 200);

    if (std::holds_alternative<vse::calibration::IVResult>(result)) {
        auto iv = std::get<vse::calibration::IVResult>(result);
        std::cout << "Implied vol = " << iv.sigma << " (expected 0.2), iterations = " << iv.iterations << std::endl;
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

    // ---- Test calibrateSSVI ----
    std::cout << "\n====== SSVI Calibration Test ======" << std::endl;

    // True params we want to recover
    vse::calibration::SSVIParams true_params{ .rho=-0.3, .eta=0.5, .gamma=0.3, .nu=0.04 };

    // Build grids
    std::vector<double> k_vec = { -0.4, -0.2, -0.1, 0.0, 0.1, 0.2, 0.4 };
    std::vector<double> T_vec = { 0.25, 0.5, 1.0, 2.0 };

    // Generate synthetic market IV matrix (rows=T, cols=k)
    Eigen::MatrixXd iv_market(T_vec.size(), k_vec.size());
    for (size_t i = 0; i < T_vec.size(); ++i) {
        for (size_t j = 0; j < k_vec.size(); ++j) {
            iv_market(i, j) = vse::calibration::impliedVolSSVI(k_vec[j], T_vec[i], true_params);
        }
    }

    std::cout << "Synthetic IV matrix:" << std::endl << iv_market << std::endl;

    // Calibrate
    auto calib_result = vse::calibration::calibrateSSVI(k_vec, T_vec, iv_market);

    if (std::holds_alternative<vse::calibration::SSVIParams>(calib_result)) {
        auto cp = std::get<vse::calibration::SSVIParams>(calib_result);
        std::cout << "Calibration succeeded!" << std::endl;
        std::cout << "  rho   = " << cp.rho   << " (true: " << true_params.rho   << ")" << std::endl;
        std::cout << "  eta   = " << cp.eta   << " (true: " << true_params.eta   << ")" << std::endl;
        std::cout << "  gamma = " << cp.gamma << " (true: " << true_params.gamma << ")" << std::endl;
        std::cout << "  nu    = " << cp.nu    << " (true: " << true_params.nu    << ")" << std::endl;
    } else {
        auto err = std::get<vse::calibration::SSVIError>(calib_result);
        if (err == vse::calibration::SSVIError::DidNotConverge)
            std::cout << "Calibration error: did not converge" << std::endl;
        else if (err == vse::calibration::SSVIError::ArbitrageViolation)
            std::cout << "Calibration error: arbitrage violation" << std::endl;
        else
            std::cout << "Calibration error: invalid input" << std::endl;
    }

    // ---- Test Local Vol (Dupire) ----
    std::cout << "\n====== Local Vol Test ======" << std::endl;

    std::cout << "sigma_loc(0.0, 1.0) = " << vse::calibration::localVol(0.0, 1.0, true_params) << std::endl;
    std::cout << "sigma_loc(-0.3, 0.5) = " << vse::calibration::localVol(-0.3, 0.5, true_params) << std::endl;
    std::cout << "sigma_loc(0.3, 0.5) = " << vse::calibration::localVol(0.3, 0.5, true_params) << std::endl;

    std::vector<double> k_lv = { -0.3, -0.1, 0.0, 0.1, 0.3 };
    std::vector<double> T_lv = { 0.25, 0.5, 1.0, 2.0 };
    Eigen::MatrixXd lv_grid = vse::calibration::localVolGrid(k_lv, T_lv, true_params);
    std::cout << "Local vol grid:" << std::endl << lv_grid << std::endl;
}