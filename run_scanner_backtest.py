#!/usr/bin/env python3
"""Backtest the morning scanner + 1m Micro Pullback ensemble on the broad universe.

Pipeline: broad volume-filtered universe -> 1m data -> 10:00 scanner picks
top-K in-play symbols per day (gap / early move / early relative volume, all
known at selection time) -> the strategy may only trade those picks, after
10:00. Reports scanner-vs-no-scanner, sweeps K and thresholds lightly, and
prints train/validation splits and the SPY benchmark.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict

import pandas as pd
import yfinance as yf

from engine.backtest import simulate_symbol, run_portfolio
from engine.data import fetch_intraday, _session
from engine.metrics import compute_metrics, spy_benchmark
from engine.optimize import prepare, _split_enriched
from engine.scanner import day_features, build_allowlist, inject_scan_ok
from engine.strategy import with_
from engine.universe import build_universe
from run_1m_broad import CONFIGS
from run_backtest import RESULTS


def collect(enriched, configs, part=None):
    trades, seen = [], set()
    for p in configs:
        for (sym, iv), E in enriched.items():
            if iv != "1m":
                continue
            if part is not None:
                cut = int(E["day"].max() * 0.7) + 1
                E = _split_enriched(E, cut, part)
                if len(E["open"]) < 100:
                    continue
            for t in simulate_symbol(sym, E, p):
                key = (t.symbol, str(t.entry_time))
                if key not in seen:
                    seen.add(key)
                    trades.append(t)
    curve, tdf = run_portfolio(trades, max_concurrent=6, sizing_mode="risk",
                               risk_per_trade_pct=1.5, pos_leverage_cap=2.5)
    return compute_metrics(curve, tdf), curve, tdf


def main() -> None:
    print("=== 1. Universe + 1m data (cached) ===")
    symbols = build_universe(min_dollar_vol_m=150.0, min_price=5.0, top_n=150)
    session = _session()
    data = {}
    for sym in symbols:
        df = fetch_intraday(sym, "1m", 29, session=session)
        if len(df) > 1000:
            data[(sym, "1m")] = df
    enriched = prepare(data)
    print(f"  {len(enriched)} frames")

    print("\n=== 2. Scanner features ===")
    feats = day_features(enriched)
    feats.to_csv(os.path.join(RESULTS, "scanner_day_features.csv"), index=False)
    print(f"  {len(feats)} (symbol, day) rows across {feats['day'].nunique()} days")

    print("\n=== 3. Scanner sweep (top-K per day) ===")
    scan_cfgs = [with_(p, scanner_filter=True) for p in CONFIGS]
    results = []
    for top_k in [5, 8, 12]:
        for gap_min, move_min, rv_min in [(0.02, 0.015, 1.5), (0.03, 0.02, 2.0)]:
            allow = build_allowlist(feats, gap_min, move_min, rv_min, top_k)
            inject_scan_ok(enriched, allow)
            m, _, _ = collect(enriched, scan_cfgs)
            results.append({"top_k": top_k, "gap_min": gap_min, "move_min": move_min,
                            "rv_min": rv_min, "picks": len(allow), **{
                                k: m[k] for k in ("total_return_pct", "profit_factor",
                                                  "win_rate", "n_trades", "max_dd_pct")}})
            r = results[-1]
            print(f"  K={top_k} gap>={gap_min} move>={move_min} rv>={rv_min} "
                  f"({r['picks']} picks) | ret {r['total_return_pct']:+8.2f}% "
                  f"pf {r['profit_factor']:4.2f} wr {r['win_rate']:2.0f}% "
                  f"n={r['n_trades']:3} dd {r['max_dd_pct']:+.2f}%")
    pd.DataFrame(results).to_csv(os.path.join(RESULTS, "scanner_sweep.csv"), index=False)

    best = max(results, key=lambda r: r["total_return_pct"])
    print(f"\n=== 4. Best scanner config: K={best['top_k']} gap>={best['gap_min']} "
          f"move>={best['move_min']} rv>={best['rv_min']} ===")
    allow = build_allowlist(feats, best["gap_min"], best["move_min"], best["rv_min"],
                            best["top_k"])
    inject_scan_ok(enriched, allow)
    out = {}
    for name, part in [("full", None), ("train", "train"), ("validation", "validation")]:
        m, curve, tdf = collect(enriched, scan_cfgs, part)
        out[name] = m
        print(f"  {name:>10}: ret {m['total_return_pct']:+7.2f}%  pf {m['profit_factor']:.2f}  "
              f"n={m['n_trades']}  wr {m['win_rate']:.0f}%  dd {m['max_dd_pct']:.2f}%")
        if part is None:
            tdf.to_csv(os.path.join(RESULTS, "scanner_trades.csv"), index=False)
            curve.to_csv(os.path.join(RESULTS, "scanner_equity.csv"))
            win_start, win_end = curve.index[0], curve.index[-1]

    m_ns, _, _ = collect(enriched, CONFIGS)   # no scanner, same universe
    print(f"  (same universe, no scanner: {m_ns['total_return_pct']:+.2f}%, "
          f"pf {m_ns['profit_factor']:.2f})")

    spy_raw = yf.download("SPY", period="90d", interval="1d", progress=False,
                          auto_adjust=True, session=session)
    if isinstance(spy_raw.columns, pd.MultiIndex):
        spy_raw.columns = spy_raw.columns.get_level_values(0)
    spy_ret = spy_benchmark(spy_raw, win_start, win_end)
    print(f"  SPY same window: {spy_ret:+.2f}%")

    with open(os.path.join(RESULTS, "scanner_summary.json"), "w", encoding="utf-8") as f:
        json.dump({"best_scanner": {k: best[k] for k in ("top_k", "gap_min", "move_min",
                                                         "rv_min")},
                   "metrics": {k: {kk: vv for kk, vv in v.items()
                                   if isinstance(vv, (int, float))} for k, v in out.items()},
                   "no_scanner_ret_pct": m_ns["total_return_pct"],
                   "spy_ret_pct": spy_ret,
                   "configs": [asdict(p) for p in scan_cfgs]}, f, indent=2)
    print("  saved results/scanner_*.csv/json")


if __name__ == "__main__":
    main()
