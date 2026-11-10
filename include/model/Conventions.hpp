#ifndef CONVENTIONS_HPP
#define CONVENTIONS_HPP

namespace vse::model {

// Comment le sous-jacent est regle. C'est une donnee d'execution, pas une macro de
// compilation : deux conventions doivent pouvoir cohabiter dans le meme binaire.
enum class Settlement {
    LinearUsd,    // payoff en dollars (actions, indices)
    InverseCoin   // payoff dans la devise du sous-jacent (Deribit BTC/ETH)
};

struct MarketConventions {
    Settlement settlement = Settlement::LinearUsd;

    [[nodiscard]] bool is_inverse() const noexcept {
        return settlement == Settlement::InverseCoin;
    }
};

} // namespace vse::model

#endif
