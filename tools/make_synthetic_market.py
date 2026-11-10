#!/usr/bin/env python3
"""Genere un marche synthetique au format historique (quotes.csv + spot.txt).

Le monde genere suit exactement le modele SSVI du moteur : si la chaine (inversion
d'IV, grille commune, calibration) est correcte, elle doit retrouver les parametres
d'entree. C'est la demonstration reproductible du framework general, sans aucune
donnee de marche sous licence.

    tools/make_synthetic_market.py --out data/csv/SYN
    ./build/calibrate_spx_minimal data/csv/SYN 2026-10-28

Stdlib uniquement (aucune dependance a installer).
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

# --- modele -----------------------------------------------------------------


def theta(T: float, nu: float) -> float:
    return nu * T


def phi(th: float, eta: float, gamma: float) -> float:
    return eta / (th**gamma * (1.0 + th) ** (1.0 - gamma))


def total_variance(k: float, T: float, rho: float, eta: float, gamma: float, nu: float) -> float:
    th = theta(T, nu)
    ph = phi(th, eta, gamma)
    return 0.5 * th * (1.0 + rho * ph * k + math.sqrt((ph * k + rho) ** 2 + 1.0 - rho * rho))


def implied_vol(k: float, T: float, rho, eta, gamma, nu) -> float:
    return math.sqrt(total_variance(k, T, rho, eta, gamma, nu) / T)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black76(is_call: bool, F: float, K: float, T: float, sigma: float, df: float = 1.0) -> float:
    if sigma <= 0.0 or T <= 0.0:
        return df * max(F - K, 0.0) if is_call else df * max(K - F, 0.0)
    s = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * s * s) / s
    d2 = d1 - s
    if is_call:
        return df * (F * norm_cdf(d1) - K * norm_cdf(d2))
    return df * (K * norm_cdf(-d2) - F * norm_cdf(-d1))


# --- generation -------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("data/csv/SYN"))
    ap.add_argument("--spot", type=float, default=5000.0)
    ap.add_argument("--rho", type=float, default=-0.35)
    ap.add_argument("--eta", type=float, default=0.70)
    ap.add_argument("--gamma", type=float, default=0.30)
    ap.add_argument("--nu", type=float, default=0.045)
    ap.add_argument("--taus", default="0.10,0.25,0.50,1.00,2.00",
                    help="maturites en annees, separees par des virgules")
    ap.add_argument("--kmin", type=float, default=-0.40)
    ap.add_argument("--kmax", type=float, default=0.40)
    ap.add_argument("--nk", type=int, default=17)
    ap.add_argument("--half-spread", type=float, default=0.005,
                    help="demi-spread relatif au mid")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    rng = random.Random(a.seed)          # deterministe : le fixture est reproductible
    taus = [float(t) for t in a.taus.split(",") if t.strip()]
    ks = [a.kmin + (a.kmax - a.kmin) * i / (a.nk - 1) for i in range(a.nk)]

    # Taux et dividendes nuls : le runner historique suppose r = q = 0, donc F = S.
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "spot.txt").write_text(f"{a.spot:.6f}\n", encoding="utf-8")

    rows = ["K,T,isCall,exerciseType,bid,ask,volume,openInterest"]
    for T in taus:
        for k in ks:
            sigma = implied_vol(k, T, a.rho, a.eta, a.gamma, a.nu)
            for is_call in (True, False):
                K = round(a.spot * math.exp(k) * 2.0) / 2.0      # strikes a 0.5 pres
                mid = black76(is_call, a.spot, K, T, sigma)
                if mid <= 0.0:
                    continue
                half = max(a.half_spread * mid, 0.02)
                bid, ask = max(mid - half, 0.0), mid + half
                if bid <= 0.0:                                    # quote non coteable
                    continue
                vol = rng.randint(5, 400)
                oi = rng.randint(10, 5000)
                rows.append(f"{K:.1f},{T:.6f},{1 if is_call else 0},European,"
                            f"{bid:.4f},{ask:.4f},{vol},{oi}")

    (a.out / "quotes.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (a.out / "params.json").write_text(json.dumps({
        "rho": a.rho, "eta": a.eta, "gamma": a.gamma, "nu": a.nu,
        "spot": a.spot, "taus": taus, "note": "parametres vrais, a retrouver par calibration",
    }, indent=2) + "\n", encoding="utf-8")

    print(f"{len(rows) - 1} quotes ecrites dans {a.out}")
    print(f"parametres vrais : rho={a.rho} eta={a.eta} gamma={a.gamma} nu={a.nu}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
