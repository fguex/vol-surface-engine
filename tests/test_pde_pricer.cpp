// Purpose
// Turn the current PDE assertions into real regression tests.
//
// What should live here
// - Grid construction sanity checks.
// - Thomas solver correctness on tiny linear systems.
// - European flat-vol PDE price vs analytic Black-Scholes.
// - American put >= European put.
// - Finite Greeks on an ATM reference case.
//
// Suggested first tests
// 1. uniform_grid_has_expected_spacing_and_nodes
// 2. thomas_solver_matches_known_solution
// 3. european_pde_matches_black_scholes_under_flat_vol
// 4. american_put_is_not_cheaper_than_european_put
//
// No live SPX calibration should appear in this file.
