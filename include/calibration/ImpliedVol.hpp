#ifndef IMPLIEDVOL_HPP
#define IMPLIEDVOL_HPP
#include "pricing/blackscholes.hpp"
#include <variant>

namespace vse::calibration{

struct IVResult{double sigma; int iterations;};
enum class IVError {
    NoArbitrageViolation,
    DidNotConverge,
    InvalidInput
};
enum class OptionType {Call, Put};
std::variant<IVResult, IVError> impliedVol(
    double                       marketPrice,
    vse::pricing::BSParams       params,
    OptionType                    type,
    double                        sigma_lo,
    double                        sigma_hi,
    double                        tol,
    int                           maxIter
);

}
#endif 