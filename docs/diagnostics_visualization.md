SSVI diagnostics workflow

1. Build/run the minimal runner and ask it for a JSON artifact.
2. Plot the artifact with the Python script.
3. Share 3 PNGs and the summary.txt with Sylvestre.

Runner
- Source: apps/calibrate_spx_minimal.cpp
- Optional flag: --json-out <path>

Example commands
- c++ -std=c++20 -Iinclude -I/opt/homebrew/include/eigen3 apps/calibrate_spx_minimal.cpp build/libvol_surface_engine.a -L/opt/homebrew/lib -lnlopt -o ./calibrate_spx_minimal
- ./calibrate_spx_minimal --json-out docs/examples/ssvi_spx_diag.json

Plotter
- Source: scripts/plot_ssvi_diagnostics.py
- Input: JSON artifact from the runner
- Output:
  - smile_overlays.png
  - residual_heatmap.png
  - rmse_by_maturity.png
  - summary.txt

Recommended interpretation
- smile_overlays.png
  - tells you whether the model misses ATM, skew, or wings
- residual_heatmap.png
  - tells you where the model is systematically biased across (T, k)
- rmse_by_maturity.png
  - tells you which maturities drive the total error
- eta_ratio in summary.txt
  - if always very close to 1, suspect model rigidity or conservative constraints before blaming the optimizer
