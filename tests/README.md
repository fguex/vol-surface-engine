Test plan for the C++ engine

Goal
- Move all validation logic out of src/main.cpp.
- Make every numerical invariant executable through a dedicated test target.
- Keep tests small, deterministic, and named by behavior.

Suggested first wave
- test_blackscholes.cpp: pricing identities and stable edge cases.
- test_implied_vol.cpp: inversion round-trip and error handling.
- test_ssvi_synthetic.cpp: recover known parameters on synthetic data.
- test_local_vol.cpp: positivity / finite values on controlled grids.
- test_pde_pricer.cpp: flat-vol European vs Black-Scholes, American >= European.
- test_market_data.cpp: parsing and failure modes.

Rules
- One behavior per test.
- Prefer synthetic data over live market data.
- No calibration-on-real-data test in the fast unit suite.
- Every future bug fix starts with a failing regression test.
