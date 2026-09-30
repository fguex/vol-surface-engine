Quant fitting state and next business steps

Where you are now
- You load curated SPX quotes from data/csv/SPX.
- You compute implied vols quote by quote from mid prices.
- You use simple market assumptions in the runner: flat r = 0, q = 0.
- You keep only quotes near ATM in log-moneyness.
- You group quotes by maturity, keep slices with enough points, then keep only bi-lateral slices.
- You intersect maturities onto one common k-grid and interpolate each slice onto that grid.
- You calibrate one global 4-parameter SSVI model with theta(T) = nu * T.
- The optimizer works under arbitrage-certifying bounds through eta = u * etaMax(gamma, rho).

What this means business-wise
- You are not yet fitting a fully flexible surface.
- You are fitting a very constrained global surface.
- If the optimum keeps pushing u close to 1, that often means one of these:
  1. the smile wings want more curvature/skew than your admissible region allows
  2. theta(T) = nu*T is too rigid for the real term structure
  3. the objective in raw implied-vol errors is pulling too hard on some slices
  4. preprocessing/filtering/interpolation is distorting what the model sees
  5. your arbitrage conditions are sufficient and conservative, not exact

Important interpretation
- Hitting the boundary does not automatically mean the optimizer is bad.
- It often means the model is trying to use all available freedom.
- In your current setup, the first suspect is the parametrization, not Nelder-Mead.

Clean next steps
1. Add diagnostics before changing the model.
   - print u = eta / etaMax(gamma, rho)
   - print fit error by maturity
   - print fit error ATM vs wings
   - print min/max k kept per slice

2. Prove the calibration stack on synthetic data.
   - generate vols from known SSVI params
   - calibrate back
   - verify recovered params are not artificially pinned to the boundary

3. Revisit the parametrization.
   - strongest business candidate: stop forcing theta(T) = nu*T
   - instead let total variance term structure be more flexible, for example one theta per maturity or a smoother dedicated curve

4. Revisit the objective.
   - compare unweighted IV least squares vs weights by spread / vega / liquidity
   - normalize per maturity so one dense slice does not dominate everything

5. Only after that compare optimizers.
   - NLopt BOBYQA vs your own Nelder-Mead on the same synthetic problem
   - optimizer comparison is meaningful only once the model and diagnostics are clean

Practical recommendation for the next coding session
- Do not start by finishing Nelder-Mead.
- Start by extracting the current live calibration path into a dedicated calibrate_spx_main.cpp and add fit diagnostics.
- Then write the synthetic SSVI calibration test.
- After that, decide whether the boundary issue is optimizer-related or model-related.