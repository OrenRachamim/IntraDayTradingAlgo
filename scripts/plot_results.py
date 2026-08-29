"""Plot a backtest equity curve vs SPY buy & hold.

Usage: python scripts/plot_results.py results/base [results/other ...] --out docs/img/equity.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vcp.data import DATA_DIR


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", help="result directories containing equity.csv")
    ap.add_argument("--out", default="docs/img/equity.png")
    ap.add_argument("--log", action="store_true", default=True)
    args = ap.parse_args()

    fig, (ax, axd) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1]})
    spy = pd.read_parquet(DATA_DIR / "benchmark" / "SPY.parquet").set_index("date")["adj_close"]

    first_eq = None
    for run in args.runs:
        eq = pd.read_csv(Path(run) / "equity.csv", index_col=0, parse_dates=True).iloc[:, 0]
        if first_eq is None:
            first_eq = eq
        ax.plot(eq.index, eq / eq.iloc[0], label=Path(run).name, linewidth=1.4)
        dd = eq / eq.cummax() - 1
        axd.plot(dd.index, dd * 100, linewidth=0.9)

    spy_w = spy.reindex(first_eq.index).ffill()
    ax.plot(spy_w.index, spy_w / spy_w.iloc[0], label="SPY buy&hold",
            color="gray", linewidth=1.2, linestyle="--")
    sdd = spy_w / spy_w.cummax() - 1
    axd.plot(sdd.index, sdd * 100, color="gray", linewidth=0.8, linestyle="--")

    if args.log:
        ax.set_yscale("log")
    ax.set_ylabel("growth of $1 (log)")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3)
    axd.set_ylabel("drawdown %")
    axd.grid(alpha=0.3)
    fig.tight_layout()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
