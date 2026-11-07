"""DPC lab 2 figures from the outputs of dpc_lr2_compute.py (results/dpc-lr2/*.json -> *.png).

    uv run --no-project --python 3.13 --with matplotlib python experiments/dpc_lr2_plots.py

speedup.png   S_p (medians) for p = 1, 2, 4: block 10^6 (base) and 10^4 (the hypothesis), ideal S_p = p
accuracy.png  abs error versus n, log-log, three methods, [0; 14] and [0; 10]
"""

import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "results" / "dpc-lr2"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID = "#1f1f1e", "#6b6a63", "#e4e3dc"

plt.rcParams.update({"font.size": 11, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
                     "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False})


def medians(label: str) -> dict[int, float]:
    rows = json.loads((OUT / f"bench-{label}.json").read_text())
    return {p: statistics.median(r["seconds"] for r in rows if r["workers"] == p) for p in (1, 2, 4)}


def speedup() -> None:
    fig, ax = plt.subplots(figsize=(6.4, 4.2), dpi=200)
    ps = [1, 2, 4]
    ax.plot(ps, ps, color=MUTED, lw=1, ls="--", label="ideal S_p = p")
    for label, name, color in (("base", "block 10^6", BLUE), ("block1e4", "block 10^4", ORANGE)):
        m = medians(label)
        s = [m[1] / m[p] for p in ps]
        ax.plot(ps, s, color=color, lw=2, marker="o", ms=8, label=f"{name} (T_1 = {m[1]:.2f} s)")
        ax.annotate(f"{s[-1]:.2f}", (4, s[-1]), xytext=(8, 0), textcoords="offset points", va="center", color=INK)
    ax.set_xticks(ps)
    ax.set_xlabel("processes p")
    ax.set_ylabel("speed-up S_p = T_1 / T_p")
    ax.set_ylim(0, 4.3)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.legend(frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT / "speedup.png")


def accuracy() -> None:
    rows = json.loads((OUT / "accuracy.json").read_text())
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.0), dpi=200, sharey=True)
    for ax, b in zip(axes, (14.0, 10.0)):
        for method, color in (("midpoint", BLUE), ("trapezoid", ORANGE), ("simpson", AQUA)):
            g = [r for r in rows if r["b"] == b and r["method"] == method and r["abs_error"] > 0]
            ax.loglog([r["n"] for r in g], [r["abs_error"] for r in g], color=color, lw=2, marker="o", ms=6,
                      label=method)
        ax.set_title(f"[0; {b:g}]", color=INK)
        ax.set_xlabel("n")
        ax.grid(color=GRID, lw=0.8)
    axes[0].set_ylabel("abs error |I_n - F(b) + F(a)|")
    axes[1].legend(frameon=False)
    fig.tight_layout()
    fig.savefig(OUT / "accuracy.png")


if __name__ == "__main__":
    speedup()
    accuracy()
    print("written", OUT / "speedup.png", OUT / "accuracy.png")
