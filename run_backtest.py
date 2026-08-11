#!/usr/bin/env python3
"""End-to-end Micro Pullback pipeline: fetch -> staged optimization -> validate -> report.

Usage: python run_backtest.py [--quick]
"""
from __future__ import annotations

import os
import sys
import json
from dataclasses import asdict

import pandas as pd
import yfinance as yf

from engine.data import fetch_universe, _session
from engine.optimize import prepare, grid, run_grid, evaluate
from engine.strategy import Params, with_
from engine.metrics import spy_benchmark

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

UNIVERSE = ["TSLA", "NVDA", "AMD", "PLTR", "META", "COIN", "SMCI", "MARA",
            "HOOD", "SOFI", "RIVN", "MU", "AVGO", "NFLX", "AAPL", "AMZN"]
INTERVALS = ["1m", "5m", "15m", "30m"]

PARAM_COLS = [f for f in Params.__dataclass_fields__]
METRIC_COLS = ["score", "total_return_pct", "ann_return_pct", "n_trades", "win_rate",
               "profit_factor", "expectancy_pct", "max_dd_pct", "sharpe", "avg_bars_held"]


def save(df: pd.DataFrame, name: str) -> None:
    os.makedirs(RESULTS, exist_ok=True)
    cols = [c for c in METRIC_COLS + PARAM_COLS if c in df.columns]
    df[cols].to_csv(os.path.join(RESULTS, name), index=False)
    print(f"  saved results/{name}")


def params_from_row(row: pd.Series) -> Params:
    kw = {}
    for f, spec in Params.__dataclass_fields__.items():
        v = row[f]
        t = spec.type
        if t == "bool" or isinstance(spec.default, bool):
            kw[f] = bool(v)
        elif isinstance(spec.default, int) and not isinstance(spec.default, bool):
            kw[f] = int(v)
        elif isinstance(spec.default, float):
            kw[f] = float(v)
        else:
            kw[f] = str(v)
    return Params(**kw)


def main() -> None:
    quick = "--quick" in sys.argv
    print("=== 1. Fetching data from Yahoo Finance ===")
    data = fetch_universe(UNIVERSE, INTERVALS)
    spy_daily_raw = yf.download("SPY", period="90d", interval="1d", progress=False,
                                auto_adjust=True, session=_session())
    if isinstance(spy_daily_raw.columns, pd.MultiIndex):
        spy_daily_raw.columns = spy_daily_raw.columns.get_level_values(0)
    spy_daily = spy_daily_raw

    print("\n=== 2. Precomputing indicators ===")
    enriched = prepare(data)
    print(f"  {len(enriched)} (symbol, timeframe) frames ready")

    base = Params()

    # ---------- Iteration 1: risk geometry ----------
    print("\n=== 3. Iteration 1: timeframe x stop x target x trailing ===")
    tfs = ["5m", "15m"] if quick else ["1m", "5m", "15m", "30m"]
    g1 = []
    for tf in tfs:
        g1 += grid(with_(base, timeframe=tf),
                   stop_mode=["pullback_low", "atr", "pct"],
                   target_rr=[1.5, 2.0, 3.0],
                   trail_mode=["none", "atr", "pct"])
    df1 = run_grid(enriched, g1, "iter1")
    save(df1, "iter1_risk_geometry.csv")

    best1 = params_from_row(df1.iloc[0])
    top_tfs = list(df1.head(8)["timeframe"].unique())[:2]
    print(f"  top timeframes: {top_tfs}; best stop={best1.stop_mode} rr={best1.target_rr} "
          f"trail={best1.trail_mode}")

    # ---------- Iteration 2: signal quality ----------
    print("\n=== 4. Iteration 2: surge / volume / RSI / MACD / pullback / timing ===")
    g2 = []
    for _, row in df1.head(4).iterrows():
        b = params_from_row(row)
        g2 += grid(b,
                   momentum_min_gain_atr=[1.0, 1.5, 2.0],
                   relvol_min=[1.0, 1.3, 1.7],
                   rsi_filter=[False, True],
                   macd_filter=[False, True],
                   max_pullback_bars=[2, 3])
    # dedupe
    g2 = list(dict.fromkeys(g2))
    df2 = run_grid(enriched, g2, "iter2")
    save(df2, "iter2_signal_quality.csv")

    # ---------- Iteration 3: refinement ----------
    print("\n=== 5. Iteration 3: fine-tuning around winners ===")
    g3 = []
    for _, row in df2.head(3).iterrows():
        b = params_from_row(row)
        g3 += grid(b,
                   target_rr=[b.target_rr - 0.5, b.target_rr, b.target_rr + 0.5],
                   stop_cap_pct=[1.0, 1.5, 2.0],
                   trail_activate_rr=[0.5, 1.0],
                   entry_start_min=[9 * 60 + 35, 9 * 60 + 45],
                   entry_end_min=[12 * 60, 15 * 60 + 30])
    g3 = list(dict.fromkeys(g3))
    df3 = run_grid(enriched, g3, "iter3")
    save(df3, "iter3_fine_tuning.csv")

    # ---------- Robustness: train / validation ----------
    print("\n=== 6. Train/validation robustness check on top-10 ===")
    rows = []
    for _, row in df3.head(10).iterrows():
        p = params_from_row(row)
        tr = evaluate(enriched, p, part="train")
        va = evaluate(enriched, p, part="validation")
        rows.append({**{k: row[k] for k in PARAM_COLS},
                     "full_ret": row["total_return_pct"], "full_pf": row["profit_factor"],
                     "train_ret": tr["total_return_pct"], "train_pf": tr["profit_factor"],
                     "train_trades": tr["n_trades"],
                     "val_ret": va["total_return_pct"], "val_pf": va["profit_factor"],
                     "val_trades": va["n_trades"],
                     "robust": va["total_return_pct"] > 0 and va["profit_factor"] > 1.0})
    dfr = pd.DataFrame(rows)
    save(dfr, "robustness_train_validation.csv")

    robust = dfr[dfr["robust"]]
    chosen_row = (robust if len(robust) else dfr).iloc[0]
    chosen = params_from_row(chosen_row)
    print(f"  chosen config robust on validation: {bool(chosen_row['robust'])}")

    # ---------- Final report ----------
    print("\n=== 7. Final run + SPY benchmark ===")
    final = evaluate(enriched, chosen)
    curve, tdf = final.pop("_curve"), final.pop("_trades")
    tdf.to_csv(os.path.join(RESULTS, "final_trades.csv"), index=False)
    curve.to_csv(os.path.join(RESULTS, "final_equity_curve.csv"))

    start, end = curve.index[0], curve.index[-1]
    spy_ret = spy_benchmark(spy_daily, start, end)

    with open(os.path.join(RESULTS, "final_config.json"), "w") as f:
        json.dump(asdict(chosen), f, indent=2)

    print(json.dumps({k: round(v, 3) if isinstance(v, float) else v
                      for k, v in final.items() if k in METRIC_COLS}, indent=2))
    print(f"  SPY buy&hold same window: {spy_ret:.2f}%  | strategy: "
          f"{final['total_return_pct']:.2f}%")
    write_summary(final, chosen, spy_ret, start, end, dfr)


def write_summary(final: dict, chosen: Params, spy_ret: float,
                  start, end, dfr: pd.DataFrame) -> None:
    beat = final["total_return_pct"] - spy_ret
    lines = [
        "# Micro Pullback — Final Backtest Summary\n",
        f"Window: **{start:%Y-%m-%d} → {end:%Y-%m-%d}**  |  Universe: {len(UNIVERSE)} symbols\n",
        "## Chosen configuration\n",
        "```json", json.dumps(asdict(chosen), indent=2), "```\n",
        "## Performance (portfolio, $100k start, max 4 concurrent, costs included)\n",
        "| Metric | Value |", "|---|---|",
    ]
    for k in ["total_return_pct", "ann_return_pct", "n_trades", "win_rate", "profit_factor",
              "expectancy_pct", "max_dd_pct", "sharpe", "avg_bars_held"]:
        v = final[k]
        lines.append(f"| {k} | {v:.2f} |" if isinstance(v, float) else f"| {k} | {v} |")
    lines += [
        f"| **SPY buy & hold (same window)** | {spy_ret:.2f}% |",
        f"| **Edge vs SPY** | {beat:+.2f}pp |\n",
        "## Robustness (train 70% / validation 30% of days)\n",
        dfr.head(10).to_markdown(index=False) if len(dfr) else "n/a",
        "\n*Generated automatically by run_backtest.py*",
    ]
    with open(os.path.join(RESULTS, "SUMMARY.md"), "w") as f:
        f.write("\n".join(lines))
    print("  saved results/SUMMARY.md")


if __name__ == "__main__":
    main()
