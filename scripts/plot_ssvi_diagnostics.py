from __future__ import annotations

import argparse
import json
from pathlib import Path


def pick_representative_maturities(sorted_ts: list[float]) -> list[float]:
    if not sorted_ts:
        return []
    if len(sorted_ts) <= 3:
        return sorted_ts
    picks = [sorted_ts[0], sorted_ts[len(sorted_ts) // 2], sorted_ts[-1]]
    out: list[float] = []
    for t in picks:
        if t not in out:
            out.append(t)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot SSVI diagnostics from a JSON artifact.")
    parser.add_argument("artifact", help="Path to runner JSON artifact")
    parser.add_argument("--out-dir", default="plots/ssvi", help="Directory for PNG outputs")
    args = parser.parse_args()

    import matplotlib.pyplot as plt
    import numpy as np

    artifact_path = Path(args.artifact)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    nodes = payload["nodes"]
    slices = payload["slices"]
    params = payload["params"]
    diag = payload["diagnostics"]
    meta = payload["metadata"]

    unique_ts = sorted({float(n["T"]) for n in nodes})
    unique_ks = sorted({float(n["k"]) for n in nodes})
    rep_ts = pick_representative_maturities(unique_ts)

    nodes_by_t: dict[float, list[dict]] = {}
    for n in nodes:
        nodes_by_t.setdefault(float(n["T"]), []).append(n)
    for pts in nodes_by_t.values():
        pts.sort(key=lambda x: float(x["k"]))

    fig, axes = plt.subplots(len(rep_ts), 1, figsize=(7, 3 * max(1, len(rep_ts))), squeeze=False)
    for ax, T in zip(axes[:, 0], rep_ts):
        pts = nodes_by_t[T]
        ks = [float(p["k"]) for p in pts]
        iv_mkt = [float(p["iv_market"]) for p in pts]
        iv_model = [float(p["iv_model"]) for p in pts]
        ax.plot(ks, iv_mkt, marker="o", label="market")
        ax.plot(ks, iv_model, marker="x", label="model")
        ax.set_title(f"Smile overlay — T={T:.4f}")
        ax.set_xlabel("log-moneyness k")
        ax.set_ylabel("implied vol")
        ax.grid(True, alpha=0.3)
        ax.legend()
    fig.suptitle("SSVI smile overlays", fontsize=13)
    fig.tight_layout()
    overlay_path = out_dir / "smile_overlays.png"
    fig.savefig(overlay_path, dpi=160)
    plt.close(fig)

    T_to_idx = {t: i for i, t in enumerate(unique_ts)}
    K_to_idx = {k: i for i, k in enumerate(unique_ks)}
    residual_grid = np.full((len(unique_ts), len(unique_ks)), np.nan)
    for n in nodes:
        residual_grid[T_to_idx[float(n["T"])]][K_to_idx[float(n["k"])] ] = float(n["residual"])

    fig, ax = plt.subplots(figsize=(8, 4.5))
    im = ax.imshow(
        residual_grid,
        aspect="auto",
        origin="lower",
        extent=(unique_ks[0], unique_ks[-1], unique_ts[0], unique_ts[-1]),
        cmap="coolwarm",
    )
    ax.set_title("Residual heatmap: iv_model - iv_market")
    ax.set_xlabel("log-moneyness k")
    ax.set_ylabel("maturity T")
    fig.colorbar(im, ax=ax, label="residual")
    fig.tight_layout()
    heatmap_path = out_dir / "residual_heatmap.png"
    fig.savefig(heatmap_path, dpi=160)
    plt.close(fig)

    slice_T = [float(s["T"]) for s in slices]
    slice_rmse = [float(s["rmse"]) for s in slices]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(slice_T, slice_rmse, marker="o")
    ax.set_title("RMSE by maturity")
    ax.set_xlabel("maturity T")
    ax.set_ylabel("RMSE")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    rmse_path = out_dir / "rmse_by_maturity.png"
    fig.savefig(rmse_path, dpi=160)
    plt.close(fig)

    summary_path = out_dir / "summary.txt"
    summary_path.write_text(
        "\n".join(
            [
                f"artifact={artifact_path}",
                f"underlying_folder={meta['folder']}",
                f"date={meta['date']}",
                f"spot={meta['spot']}",
                f"rho={params['rho']}",
                f"eta={params['eta']}",
                f"gamma={params['gamma']}",
                f"nu={params['nu']}",
                f"rmse_total={diag['rmse_total']}",
                f"max_abs_error={diag['max_abs_error']}",
                f"eta_ratio={diag['eta_ratio']}",
                f"smile_overlays={overlay_path}",
                f"residual_heatmap={heatmap_path}",
                f"rmse_by_maturity={rmse_path}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
