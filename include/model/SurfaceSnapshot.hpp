#ifndef SURFACESNAPSHOT_HPP
#define SURFACESNAPSHOT_HPP

#include <Eigen/Dense>

#include <string>
#include <vector>

namespace vse::model {

// Tout ce que le moteur de calibration consomme, et RIEN de plus.
//
// C'est le contrat entre les deux moities du projet : le pipeline ecrit un snapshot
// (fichier ou base), le moteur le lit sans savoir d'ou il vient. Une seule grille en k
// partagee par toutes les maturites, comme l'exige SSVI.
struct SurfaceSnapshot {
    std::string snapshot_id;              // tracabilite : d'ou vient ce fit
    std::vector<double> k_grid;           // log-moneyness, strictement croissant
    std::vector<double> T_grid;           // maturites en annees, strictement croissant
    Eigen::MatrixXd iv;                   // (T_grid.size() x k_grid.size())
    Eigen::MatrixXd weight;               // vide => poids uniformes

    [[nodiscard]] bool empty() const noexcept { return k_grid.empty() || T_grid.empty(); }

    [[nodiscard]] bool well_formed() const noexcept {
        const auto rows = static_cast<Eigen::Index>(T_grid.size());
        const auto cols = static_cast<Eigen::Index>(k_grid.size());
        if (rows == 0 || cols == 0) return false;
        if (iv.rows() != rows || iv.cols() != cols) return false;
        if (weight.size() != 0 && (weight.rows() != rows || weight.cols() != cols))
            return false;
        return true;
    }
};

} // namespace vse::model

#endif
