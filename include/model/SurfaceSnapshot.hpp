#ifndef SURFACESNAPSHOT_HPP
#define SURFACESNAPSHOT_HPP

#include <Eigen/Dense>

#include <cmath>
#include <cstddef>
#include <string>
#include <vector>

namespace vse::model {

// Tout ce que le moteur de calibration consomme, et RIEN de plus.
//
// C'est le contrat entre les deux moities du projet : le pipeline ecrit un snapshot
// (fichier ou base), le moteur le lit sans savoir d'ou il vient. Une seule grille en k
// partagee par toutes les maturites, comme l'exige SSVI : les quatre parametres sont
// ajustes conjointement, donc sans grille commune il n'y a pas de fonction objectif.
struct SurfaceSnapshot {
    // --- identite : ce qui rend un fit rejouable et auditable ---
    std::string snapshot_id;              // "deribit-BTC-20261110T195000Z" ; vide = non tracable
    std::string underlying;               // BTC, ETH
    double      spot = 0.0;               // indice du perp, en USD

    // --- la grille : exactement ce que consomme calibrateSSVI ---
    std::vector<double> k_grid;           // log-moneyness, strictement croissant
    std::vector<double> T_grid;           // maturites en annees, strictement croissant
    Eigen::MatrixXd iv;                   // (T_grid.size() x k_grid.size()), > 0
    Eigen::MatrixXd weight;               // idem, ou vide (poids uniformes)

    // --- la structure par terme : ce qui permet de revenir au prix ---
    // Aligne sur T_grid. Sans elles, k ne peut plus etre converti en strike, et un
    // residu ne peut plus etre exprime en dollars.
    std::vector<double> forward;          // F(T_i), > 0
    std::vector<double> df;               // facteur d'actualisation, > 0 (1.0 en coin)

    // --- contrat structurel ---
    // Un code d'erreur plutot qu'un message : la fonction reste noexcept, sans
    // allocation, donc utilisable a chaque entree dans le moteur. Le texte est rendu
    // une seule fois, par l'appelant qui sait ou il se trouve (lecteur, test, runner).
    enum class Error {
        Ok,
        NoId,              // snapshot_id vide : le fit ne sera pas rejouable
        EmptyGrid,         // pas de donnee a ajuster
        IvShape,           // iv n'est pas de la forme (nb T x nb k)
        WeightShape,       // weight n'est ni vide ni de la forme (nb T x nb k)
        KNotIncreasing,    // k_grid non strictement croissant (doublon ou desordre)
        TNotIncreasing,    // idem pour les maturites
        NotFinite,         // NaN ou inf dans la grille ou la matrice
        NonPositive,       // iv, poids, forward, df ou spot <= 0
        TermShape          // forward / df non alignes sur T_grid
    };

    [[nodiscard]] Error check() const noexcept {
        if (snapshot_id.empty()) return Error::NoId;

        const auto rows = static_cast<Eigen::Index>(T_grid.size());
        const auto cols = static_cast<Eigen::Index>(k_grid.size());
        if (rows == 0 || cols == 0) return Error::EmptyGrid;

        if (iv.rows() != rows || iv.cols() != cols) return Error::IvShape;
        if (weight.size() != 0 && (weight.rows() != rows || weight.cols() != cols))
            return Error::WeightShape;

        // Strictement croissant : un doublon en k cree deux colonnes identiques, et les
        // derivees en k (Durrleman, Dupire) divisent alors par zero.
        for (std::size_t i = 1; i < k_grid.size(); ++i)
            if (!(k_grid[i] > k_grid[i - 1])) return Error::KNotIncreasing;
        for (std::size_t i = 1; i < T_grid.size(); ++i)
            if (!(T_grid[i] > T_grid[i - 1])) return Error::TNotIncreasing;

        // Un seul NaN empoisonne toute la fonction objectif : BOBYQA rend alors un point
        // arbitraire, ou reste colle a une borne sans rien signaler.
        for (double k : k_grid) if (!std::isfinite(k)) return Error::NotFinite;
        for (double T : T_grid)
            if (!std::isfinite(T) || !(T > 0.0)) return Error::NonPositive;
        if (!std::isfinite(spot) || !(spot > 0.0)) return Error::NonPositive;

        for (Eigen::Index i = 0; i < rows; ++i)
            for (Eigen::Index j = 0; j < cols; ++j) {
                const double v = iv(i, j);
                if (!std::isfinite(v)) return Error::NotFinite;
                if (!(v > 0.0)) return Error::NonPositive;   // puis w = v^2 T, phi = eta/theta^gamma
            }

        if (weight.size() != 0) {
            for (Eigen::Index i = 0; i < rows; ++i)
                for (Eigen::Index j = 0; j < cols; ++j) {
                    const double w = weight(i, j);
                    if (!std::isfinite(w)) return Error::NotFinite;
                    if (!(w > 0.0)) return Error::NonPositive;
                }
        }

        // Vide = structure par terme non renseignee (snapshot de calibration seul) ;
        // renseignee = elle doit l'etre entierement, et pour chaque maturite.
        if (!forward.empty() || !df.empty()) {
            if (forward.size() != T_grid.size() || df.size() != T_grid.size())
                return Error::TermShape;
            for (std::size_t i = 0; i < T_grid.size(); ++i) {
                if (!std::isfinite(forward[i]) || !(forward[i] > 0.0)) return Error::NonPositive;
                if (!std::isfinite(df[i]) || !(df[i] > 0.0)) return Error::NonPositive;
            }
        }
        return Error::Ok;
    }

    [[nodiscard]] bool well_formed() const noexcept { return check() == Error::Ok; }

    // Rien a ajuster. A distinguer d'un snapshot MAL FORME : le premier est un cas
    // normal (aucune donnee exploitable a cet instant), le second est un bug d'ecriture.
    [[nodiscard]] bool empty() const noexcept { return k_grid.empty() || T_grid.empty(); }

    static const char* describe(Error e) noexcept {
        switch (e) {
            case Error::Ok:             return "ok";
            case Error::NoId:           return "snapshot_id vide : fit non tracable";
            case Error::EmptyGrid:      return "grille k ou T vide";
            case Error::IvShape:        return "iv n'a pas la forme (nb T x nb k)";
            case Error::WeightShape:    return "weight n'est ni vide ni de la forme (nb T x nb k)";
            case Error::KNotIncreasing: return "k_grid n'est pas strictement croissant";
            case Error::TNotIncreasing: return "T_grid n'est pas strictement croissant";
            case Error::NotFinite:      return "NaN ou inf dans la grille ou la matrice";
            case Error::NonPositive:    return "valeur <= 0 (iv, poids, forward, df ou spot)";
            case Error::TermShape:      return "forward / df non alignes sur T_grid";
        }
        return "inconnu";
    }
};

} // namespace vse::model

#endif
