#!/usr/bin/env python3
"""Run the winning 1-minute Micro Pullback configuration on the broad
volume-filtered US universe (S&P 500 + Nasdaq-100 + liquid high-beta extras).

Universe: top symbols by average daily dollar volume (default >= $150M/day,
price >= $5, top 150). Strategy: the two 1m configs that survived the
run_1m_study.py robustness stage, traded as one deduped ensemble.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict

import pandas as pd
import yfinance as yf

from engine.backtest import simulate_symbol, run_portfolio
from engine.data import fetch_intraday, _session
from engine.metrics import compute_metrics, spy_benchmark
from engine.optimize import prepare, _split_enriched
from engine.strategy import Params, with_
from engine.universe import build_universe
from run_backtest import RESULTS

FULL = 15 * 60 + 30

CONFIG_1M = Params(timeframe="1m", momentum_mode="surge", pullback_def="lower_high",
                   relvol_min=1.7, momentum_min_gain_atr=1.5, stop_mode="pullback_low",
                   trail_mode="none", target_rr=3.0, entry_end_min=FULL,
                   macd_filter=True, max_pullback_bars=2,
                   in_play_filter=True, in_play_gain_adr=0.3, in_play_relvol=1.2,
                   sizing_mode="risk", risk_per_trade_pct=1.5, pos_leverage_cap=2.5,
                   max_concurrent=6)
CONFIGS = [CONFIG_1M, with_(CONFIG_1M, rsi_filter=True)]


def collect_trades(enriched: dict, part=None):
    trades, seen = [], set()
    for p in CONFIGS:
        for (sym, iv), E in enriched.items():
            if iv != "1m":
                continue
            if part is not None:
                cut = int(E["day"].max() * 0.7) + 1
                E = _split_enriched(E, cut, part) if isinstance(part, str) else _split_enriched(E, part)
                if len(E["open"]) < 100:
                    continue
            for t in simulate_symbol(sym, E, p):
                key = (t.symbol, str(t.entry_time))
                if key not in seen:
                    seen.add(key)
                    trades.append(t)
    return trades


def main() -> None:
    top_n = int(sys.argv[sys.argv.index("--top")+1]) if "--top" in sys.argv else 150
    print("=== 1. Building volume-filtered universe ===")
    symbols = build_universe(min_dollar_vol_m=150.0, min_price=5.0, top_n=top_n)
    print(f"  {len(symbols)} symbols: {', '.join(symbols[:15])} ...")

    print("\n=== 2. Fetching 1m data (chunked, cached) ===")
    session = _session()
    data = {}
    for k, sym in enumerate(symbols):
        df = fetch_intraday(sym, "1m", 29, session=session)
        if len(df) > 1000:
            data[(sym, "1m")] = df
        if (k + 1) % 25 == 0:
            print(f"  {k + 1}/{len(symbols)} fetched ({len(data)} usable)")
    print(f"  usable 1m frames: {len(data)}")

    print("\n=== 3. Indicators ===")
    enriched = prepare(data)
    win_start = min(E["index"][0] for E in enriched.values())
    win_end = max(E["index"][-1] for E in enriched.values())

    print("\n=== 4. Backtest (ensemble of 2 robust 1m configs) ===")
    out = {}
    for name, part in [("full", None), ("train", "train"), ("validation", "validation")]:
        trades = collect_trades(enriched, part)
        curve, tdf = run_portfolio(trades, max_concurrent=6, sizing_mode="risk",
                                   risk_per_trade_pct=1.5, pos_leverage_cap=2.5)
        m = compute_metrics(curve, tdf)
        out[name] = m
        print(f"  {name:>10}: ret {m['total_return_pct']:+7.2f}%  pf {m['profit_factor']:.2f}  "
              f"n={m['n_trades']}  wr {m['win_rate']:.0f}%  dd {m['max_dd_pct']:.2f}%  "
              f"sharpe {m['sharpe']:.2f}")
        if part is None:
            tdf.to_csv(os.path.join(RESULTS, "broad1m_trades.csv"), index=False)
            curve.to_csv(os.path.join(RESULTS, "broad1m_equity.csv"))
            taken = tdf[tdf["taken"]]
            by_sym = taken.groupby("symbol")["ret_pct"].agg(["count", "sum"]) \
                .sort_values("sum", ascending=False)
            print(f"  symbols traded: {len(by_sym)} | top contributors: "
                  + ", ".join(f"{s} ({r['sum']:+.1f}%/{int(r['count'])}t)"
                              for s, r in by_sym.head(5).iterrows()))

    print("\n=== 5. SPY benchmark over the same window ===")
    spy_raw = yf.download("SPY", period="90d", interval="1d", progress=False,
                          auto_adjust=True, session=session)
    if isinstance(spy_raw.columns, pd.MultiIndex):
        spy_raw.columns = spy_raw.columns.get_level_values(0)
    spy_ret = spy_benchmark(spy_raw, win_start, win_end)
    print(f"  window {win_start:%Y-%m-%d} -> {win_end:%Y-%m-%d}: SPY {spy_ret:+.2f}%  "
          f"strategy {out['full']['total_return_pct']:+.2f}%  "
          f"(edge {out['full']['total_return_pct'] - spy_ret:+.2f}pp)")

    with open(os.path.join(RESULTS, "broad1m_summary.json"), "w") as f:
        json.dump({"universe_size": len(symbols), "usable_frames": len(data),
                   "window": [str(win_start), str(win_end)], "spy_ret_pct": spy_ret,
                   "configs": [asdict(p) for p in CONFIGS],
                   "metrics": {k: {kk: vv for kk, vv in v.items()
                                   if isinstance(vv, (int, float))} for k, v in out.items()}},
                  f, indent=2)
    print("  saved results/broad1m_*.csv/json")


if __name__ == "__main__":
    main()
