Nelder-Mead next steps

Current state
- src/opt/NelderMead.cpp is unfinished.
- include/opt/NelderMead.hpp does not match the .cpp signature today.
- The build passes only because this optimizer is not driving the calibration path yet.

Clean way to resume
1. First decide if you really need a custom Nelder-Mead.
   - If NLopt BOBYQA is enough, keep Nelder-Mead as a learning module only.
   - If you want ownership and experimentation, finish it as a standalone optimizer with tests.

2. Before coding, write tiny synthetic tests for the optimizer itself.
   - quadratic bowl in 2D
   - shifted quadratic in 4D
   - stop criteria on x-spread and f-spread

3. Fix the API first.
   - choose one signature and keep header / implementation aligned
   - prefer: minimize(f, x0, params) returning best x

4. Only then implement the missing steps.
   - simplex ordering
   - centroid
   - reflection
   - expansion
   - contraction
   - shrink
   - stopping criterion

5. Do not plug it into SSVI calibration until it passes its own tests.

Business note
- A custom optimizer will not solve a bad parametrization by itself.
- If the fitted solution sits on the arbitrage boundary, the first suspect is usually the model/objective/filters, not the optimizer.
