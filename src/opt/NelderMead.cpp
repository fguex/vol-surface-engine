#include "opt/NelderMead.hpp"
#include <vector>
#include <algorithm>
#include <numeric>
#include <cmath>

namespace vse::opt{

std::vector<double> minimize(
    std::function<double(const std::vector<double>&)> f,
    const std::vector<double>& x0,
    const NMParams& params)
{
    const int n = static_cast<int>(x0.size());  // dimension du problème

    // ── 1. Construire le simplexe initial (n+1 sommets) ──────────────────
    // sommet[0] = x0
    // sommet[i+1] = x0 avec x0[i] perturbé d'un step
    // Évaluer f sur chaque sommet et stocker dans Vertex.f
    Simplex s;
    Vertex v0;
    v0.x = x0;
    v0.f = f(x0);
    s.vertices.push_back(v0);

    for (int i = 0; i < n; ++i){
        Vertex v;
        v.x = x0;
        v.x[i] += 0.05;
        v.f = f(v.x);
        s.vertices.push_back(v);
    }
    // TODO : remplir s.vertices (n+1 Vertex), chacun avec x et f=f(x)

    // ── 2. Boucle principale ──────────────────────────────────────────────
    for (int iter = 0; iter < params.max_iter; ++iter) {

        // a. Trier les sommets par f croissant
        //    après tri : vertices[0] = meilleur, vertices[n] = pire
        // TODO : std::sort sur s.vertices selon .f
        std::sort(s.vertices.begin(), s.vertices.end(),  [](const Vertex& a, const Vertex& b) {
        return a.f < b.f;   // trier par valeur f croissante
         });
         
        /* WIP -- critère d'arrêt (à corriger)
        double max_dist = 0.0;
        double max_f = s.vertices[n].f;
        double min_f = s.vertices[0].f;
        for (int i = 0; i < n; ++i){
            double eval = s.vertices[i].f;   // f déjà calculé, pas besoin de rappeler f()
            if (eval > max_f) max_f = eval;
            if (eval < min_f) min_f = eval;

            for (int j = 0; j < n - i; ++j){
                // distance entre sommets : s.vertices[i].x et s.vertices[j].x sont des vecteurs
                // il faut une norme, pas une soustraction directe
                if (s.vertices[i].x - s.vertices[j].x > max_dist){   // TODO : norme euclidienne
                    max_dist = s.vertices[i].x - s.vertices[j].x;
                }
            }
        }
        bool dist_x_test = (max_dist < params.tol_x);
        bool dist_f_test = (max_f - min_f < params.tol_f);
        if (dist_x_test && dist_f_test) break;
        */

        // b. Critère d'arrêt : LES DEUX simultanément
        //    étalement des x  : max distance entre sommets < tol_x
        //    étalement des f  : max(f) - min(f) < tol_f
        // TODO : calculer et tester les deux conditions, break si OK

        // c. Centroïde des n meilleurs (exclure vertices[n], le pire)
        //    x_bar[k] = (1/n) * sum_{i=0}^{n-1} vertices[i].x[k]
        // TODO : vecteur x_bar de taille n

        // d. Réflexion
        //    xr = x_bar + alpha * (x_bar - pire)
        //    évaluer fr = f(xr)
        // TODO

        // e. Arbre de décision
        //
        //    si fr < f(meilleur) :
        //        expansion : xe = x_bar + gamma * (xr - x_bar)
        //        si f(xe) < fr → remplacer pire par xe
        //        sinon         → remplacer pire par xr
        //
        //    sinon si fr < f(avant-dernier) :  [vertices[n-1]]
        //        accepter : remplacer pire par xr
        //
        //    sinon :
        //        contraction :
        //            si fr < f(pire) → externe : xc = x_bar + beta*(xr - x_bar)
        //            sinon           → interne : xc = x_bar - beta*(x_bar - pire)
        //        si f(xc) < f(pire) → remplacer pire par xc
        //        sinon              → SHRINK (rétrécissement)
        //            pour i=1..n : vertices[i].x = vertices[0].x + delta*(vertices[i].x - vertices[0].x)
        //            ré-évaluer f sur tous les sommets sauf vertices[0]
        // TODO
    }

    // ── 3. Tri final et retour du meilleur point ──────────────────────────
    // TODO : trier, retourner vertices[0].x
    return {};
} 

} // end namespace