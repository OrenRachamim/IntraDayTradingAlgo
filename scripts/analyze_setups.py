"""Which setup characteristics predict winning vs losing trades?

Re-derives the setups of the final config, matches each executed trade in
results/round8_FULL/trades.csv back to the Setup that produced it (by symbol +
pivot + confirmation before entry), and reports outcome statistics bucketed by
every pattern feature. Output: results/setup_analysis.csv + console report.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vcp.config import load_config
from vcp.pipeline import DataCache, run_pipeline  # noqa: F401 (cache helpers)
from vcp.data import load_calendar, eligible_symbols, load_symbol, load_benchmark
from vcp.trend_template import rs_percentiles
from vcp.vcp_detector import detect_setups

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    cfg = load_config(ROOT / "configs" / "final.yaml")
    trades = pd.read_csv(ROOT / "results" / "round8_FULL" / "trades.csv",
                         parse_dates=["entry_date", "exit_date"])
    print(f"{len(trades)} trades loaded")

    cal = load_calendar(cfg.backtest.start, cfg.backtest.end)
    market = load_benchmark(cal, cfg.entry.market_filter_ma)
    syms_needed = sorted(trades.symbol.unique())
    data = {}
    for s in eligible_symbols(cal, cfg.universe.min_history_days):
        if s in syms_needed:
            sd = load_symbol(s, cal)
            if sd is not None:
                data[s] = sd
    # RS ranks need the full cross-section; reuse trade-time RS from entry gap instead
    import warnings
    rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for sym, g in trades.groupby("symbol"):
            if sym not in data:
                continue
            setups = detect_setups(data[sym], cfg)
            for _, tr in g.iterrows():
                # the setup that produced this trade: same pivot, confirmed before entry
                cands = [s for s in setups
                         if abs(s.pivot - tr.pivot) / tr.pivot < 1e-6
                         and s.confirm_idx < tr.entry_idx]
                if not cands:
                    continue
                s = max(cands, key=lambda x: x.confirm_idx)
                sd = data[sym]
                base_len = s.confirm_idx - s.base_start_idx
                rows.append({
                    "symbol": sym,
                    "entry_date": tr.entry_date,
                    "year": tr.entry_date.year,
                    "r": tr.r_multiple,
                    "pnl": tr.pnl,
                    "win": tr.pnl > 0,
                    "reason": tr.reason,
                    "hold": tr.exit_idx - tr.entry_idx,
                    "n_contractions": s.n_contractions,
                    "final_depth": s.depths[-1],
                    "first_depth": s.depths[0],
                    "tighten_ratio": s.depths[-1] / s.depths[0],
                    "vdu_ratio": s.vdu_ratio,
                    "base_len_days": base_len,
                    "pivot_vs_top": s.pivot / s.base_high - 1.0,
                    "wait_days": tr.entry_idx - s.confirm_idx,
                    "regime_on": bool(market.regime_ok[tr.entry_idx - 1]),
                })
    df = pd.DataFrame(rows)
    df.to_csv(ROOT / "results" / "setup_analysis.csv", index=False)
    print(f"matched {len(df)} trades to setups\n")

    def bucket_report(col: str, bins, labels=None):
        b = pd.cut(df[col], bins=bins, labels=labels)
        g = df.groupby(b, observed=True).agg(
            n=("r", "size"), win_rate=("win", "mean"), avg_R=("r", "mean"),
            med_R=("r", "median"), total_pnl=("pnl", "sum"))
        g["win_rate"] = (g["win_rate"] * 100).round(1)
        g[["avg_R", "med_R"]] = g[["avg_R", "med_R"]].round(2)
        g["total_pnl"] = (g["total_pnl"] / 1000).round(0).astype(int)
        print(f"--- by {col} (total_pnl in $K) ---")
        print(g.to_string(), "\n")

    bucket_report("n_contractions", [1.5, 2.5, 3.5, 6.5], ["2", "3", "4-6"])
    bucket_report("final_depth", [0, 0.02, 0.04, 0.06, 0.11],
                  ["<2%", "2-4%", "4-6%", "6-10%"])
    bucket_report("first_depth", [0, 0.10, 0.18, 0.36], ["<10%", "10-18%", ">18%"])
    bucket_report("vdu_ratio", [0, 0.4, 0.6, 0.8, 1.01], ["<0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"])
    bucket_report("base_len_days", [0, 30, 60, 120, 400], ["<30d", "30-60d", "60-120d", ">120d"])
    bucket_report("pivot_vs_top", [-0.16, -0.08, -0.03, 0.001], ["8-15% below", "3-8% below", "at top"])
    bucket_report("wait_days", [-1, 3, 10, 41], ["<=3d", "4-10d", ">10d"])

    print("--- by market regime at entry ---")
    g = df.groupby("regime_on").agg(n=("r", "size"), win_rate=("win", "mean"),
                                    avg_R=("r", "mean"), total_pnl=("pnl", "sum"))
    g["win_rate"] = (g["win_rate"] * 100).round(1)
    print(g.round(2).to_string(), "\n")

    print("--- by era ---")
    era = pd.cut(df.year, [2003, 2009, 2016, 2021, 2026],
                 labels=["2004-09", "2010-16", "2017-21", "2022-26"])
    g = df.groupby(era, observed=True).agg(n=("r", "size"), win_rate=("win", "mean"),
                                           avg_R=("r", "mean"), total_pnl=("pnl", "sum"))
    g["win_rate"] = (g["win_rate"] * 100).round(1)
    g["total_pnl"] = (g["total_pnl"] / 1000).round(0).astype(int)
    print(g.round(2).to_string(), "\n")

    print("--- top-decile trades: what do they share? ---")
    top = df.nlargest(max(len(df) // 10, 1), "r")
    rest = df.drop(top.index)
    for col in ["n_contractions", "final_depth", "first_depth", "vdu_ratio",
                "base_len_days", "pivot_vs_top", "wait_days", "hold"]:
        print(f"  {col:16} top10%: {top[col].mean():8.3f}   rest: {rest[col].mean():8.3f}")


if __name__ == "__main__":
    main()
