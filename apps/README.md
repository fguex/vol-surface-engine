Application split plan

Goal
- Stop using src/main.cpp as a mixed test bench, demo runner, and live calibration script.
- Keep one executable per use case.

Suggested executables
- sandbox_main.cpp
  - manual experimentation
  - temporary prints
  - quick numerical probes
  - never relied on as a test suite

- calibrate_spx_main.cpp
  - load curated SPX data
  - compute implied vols
  - build slices / common grid
  - run SSVI calibration
  - print diagnostics for fit quality and constraint usage

Rule
- If a check must always remain true, it belongs in tests/, not in a sandbox main.
