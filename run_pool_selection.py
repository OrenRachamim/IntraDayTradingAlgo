#!/usr/bin/env python3
"""Pool-based robust selection + ensemble.

Loads every per-iteration grid CSV, dedupes by signal-defining params, takes the
top-N distinct configs per timeframe, re-tests each on the 70/30 train/validation
day split, and then evaluates an ENSEMBLE of the robust survivors: all their
trades merged, each config granted risk_budget/K per trade. A diversified basket
of mediocre-but-real edges is far more stable than one optimized champion.
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import asdict

import pandas as pd
import yfinance as yf

from engine.backtest import simulate_symbol, run_portfolio
from engine.data import fetch_universe, fetch_intraday, _session
from engine.metrics import compute_metrics, spy_benchmark
from engine.optimize import prepare, evaluate, _split_enriched
from engine.strategy import Params, with_
from run_backtest import UNIVERSE, INTERVALS, PARAM_COLS, params_from_row, RESULTS

SIGNAL_COLS = [c for c in PARAM_COLS if c not in
               ("sizing_mode", "risk_per_trade_pct", "pos_leverage_cap", "max_concurrent")]


def load_pool() -> pd.DataFrame:
    frames = []
    for f in glob.glob(os.path.join(RESULTS, "iter*.csv")):
        df = pd.read_csv(f)
        if all(c in df.columns for c in SIGNAL_COLS):
            frames.append(df)
    pool = pd.concat(frames, ignore_index=True)
    pool = pool.drop_duplicates(subset=SIGNAL_COLS, keep="first")
    pool = pool[(pool["profit_factor"] > 1.05) & (pool["n_trades"] >= 25)]
    pool = pool.sort_values("score", ascending=False)
    pool = pool.groupby("timeframe", sort=False).head(8)
    return pool.reset_index(drop=True)


def ensemble_trades(enriched: dict, configs: list[Params], part: str | None = None,
                    split_frac: float = 0.7):
    """Collect trades from every config; risk is split evenly across configs."""
    all_trades = []
    k = max(len(configs), 1)
    for p in configs:
        for (sym, iv), E in enriched.items():
            if iv != p.timeframe:
                continue
            if part:
                cut = int(E["day"].max() * split_frac) + 1
                E = _split_enriched(E, cut, part)
                if len(E["open"]) < 100:
                    continue
            all_trades.extend(simulate_symbol(sym, E, p))
    curve, tdf = run_portfolio(all_trades, max_concurrent=6, sizing_mode="risk",
                               risk_per_trade_pct=1.0 / k * 3, pos_leverage_cap=1.5)
    return compute_metrics(curve, tdf), curve, tdf


def main() -> None:
    print("=== Loading data (cache) ===")
    data = fetch_universe(UNIVERSE, INTERVALS)
    spy_data = {iv: fetch_intraday("SPY", iv, 29 if iv == "1m" else 59)
                for iv in INTERVALS}
    spy_data = {k: v for k, v in spy_data.items() if len(v) > 100}
    enriched = prepare(data, spy_data)

    print("\n=== Candidate pool (deduped, diverse) ===")
    pool = load_pool()
    print(pool[["timeframe", "momentum_mode", "pullback_def", "score",
                "total_return_pct", "profit_factor", "n_trades"]].to_string())

    print("\n=== Train/validation on the pool ===")
    rows, robust_params = [], []
    for _, row in pool.iterrows():
        p = params_from_row(row)
        # normalize sizing so train/val comparison is apples-to-apples
        p = with_(p, sizing_mode="risk", risk_per_trade_pct=1.0, pos_leverage_cap=2.0,
                  max_concurrent=4)
        tr = evaluate(enriched, p, part="train")
        va = evaluate(enriched, p, part="validation")
        tr.pop("_curve"), tr.pop("_trades"), va.pop("_curve"), va.pop("_trades")
        ok = (va["total_return_pct"] > 0 and va["profit_factor"] > 1.0
              and va["n_trades"] >= 5 and tr["total_return_pct"] > 0)
        rows.append({**{c: row[c] for c in SIGNAL_COLS},
                     "train_ret": tr["total_return_pct"], "train_pf": tr["profit_factor"],
                     "train_n": tr["n_trades"], "val_ret": va["total_return_pct"],
                     "val_pf": va["profit_factor"], "val_n": va["n_trades"], "robust": ok})
        if ok:
            robust_params.append(p)
        print(f"  {p.timeframe:>3} {p.momentum_mode:>6}/{p.pullback_def:<10} "
              f"train {tr['total_return_pct']:+6.2f}% (pf {tr['profit_factor']:.2f}, n={tr['n_trades']}) | "
              f"val {va['total_return_pct']:+6.2f}% (pf {va['profit_factor']:.2f}, n={va['n_trades']}) "
              f"{'ROBUST' if ok else ''}")
    pd.DataFrame(rows).to_csv(os.path.join(RESULTS, "pool_robustness.csv"), index=False)

    if not robust_params:
        print("\nNo individually-robust configs — taking top-5 by validation for ensemble test")
        dfp = pd.DataFrame(rows).sort_values("val_ret", ascending=False).head(5)
        robust_params = [with_(params_from_row(r), sizing_mode="risk", risk_per_trade_pct=1.0,
                               pos_leverage_cap=2.0, max_concurrent=4)
                         for _, r in dfp.iterrows()]

    print(f"\n=== Ensemble of {len(robust_params)} configs ===")
    for part in ("train", "validation", None):
        m, curve, tdf = ensemble_trades(enriched, robust_params, part=part)
        name = part or "FULL"
        print(f"  {name:>10}: ret {m['total_return_pct']:+.2f}%  pf {m['profit_factor']:.2f}  "
              f"n={m['n_trades']}  dd {m['max_dd_pct']:.2f}%  sharpe {m['sharpe']:.2f}")
        if part is None:
            tdf.to_csv(os.path.join(RESULTS, "ensemble_trades.csv"), index=False)
            curve.to_csv(os.path.join(RESULTS, "ensemble_equity_curve.csv"))
            spy_raw = yf.download("SPY", period="90d", interval="1d", progress=False,
                                  auto_adjust=True, session=_session())
            if isinstance(spy_raw.columns, pd.MultiIndex):
                spy_raw.columns = spy_raw.columns.get_level_values(0)
            spy_ret = spy_benchmark(spy_raw, curve.index[0], curve.index[-1])
            print(f"  SPY buy&hold same window: {spy_ret:+.2f}%")
            with open(os.path.join(RESULTS, "ensemble_configs.json"), "w") as f:
                json.dump([asdict(p) for p in robust_params], f, indent=2)


if __name__ == "__main__":
    main()
