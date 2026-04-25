# Vol Surface Engine

C++ engine for implied volatility surface calibration and local volatility extraction for equity derivatives.

## Features
- Implied volatility inversion (Brent solver)
- SSVI surface fit with arbitrage-free constraints (Gatheral & Jacquier)
- Analytical closed-form derivatives of SSVI for numerical stability
- Local volatility extraction via Dupire formula
- Vanilla options pricing and Greeks (delta, gamma, vega, theta, rho, vanna, volga)

## Stack
- C++20
- CMake 3.20+
- Eigen 3.4
- NLopt
- GoogleTest 1.14

## Dependencies

```bash
brew install eigen nlopt
```

## Build

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

## Run tests

```bash
cd build && ctest --output-on-failure
```

## Project structure

```
vol-surface-engine/
├── include/          # Public headers
│   ├── data/         # Market data structures
│   ├── calibration/  # ImpliedVol, SSVI, LocalVol
│   ├── pricing/      # BlackScholes, Greeks
│   └── utils/        # Interpolation, Logger
├── src/              # Implementations
├── tests/            # GoogleTest unit tests
├── data/             # Sample market quotes (CSV)
└── scripts/          # Python — ML phase 2 (Heston neural calibration)
```

## Roadmap
- [ ] Phase 1 — Core calibration engine
  - [ ] Black-Scholes pricer + Greeks
  - [ ] Implied vol inversion (Brent)
  - [ ] SSVI calibration with arbitrage-free constraints
  - [ ] Local vol extraction via Dupire
  - [ ] Unit tests (GoogleTest)
- [ ] Phase 2 — Neural network calibration
  - [ ] Heston pricer (Carr-Madan)
  - [ ] Training data generation
  - [ ] PyTorch training + ONNX export
  - [ ] C++ inference via ONNX Runtime

## References
- Gatheral, J. & Jacquier, A. (2014). *Arbitrage-free SVI volatility surfaces*
- Dupire, B. (1994). *Pricing with a smile*
- Carr, P. & Madan, D. (1999). *Option valuation using the fast Fourier transform*
