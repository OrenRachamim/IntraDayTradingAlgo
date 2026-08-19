#!/usr/bin/env python3
"""Walk-forward evaluation of the in-play Micro Pullback strategy.

Protocol:
- Small doctrine-driven candidate grid (16 configs) to limit selection noise.
- Expanding-window folds: train on all days before each 8-day OOS block
  (first block starts at day 25), pick the best config by train score, then
  trade it on the OOS block. No OOS information ever reaches the selection.
- All OOS trades are concatenated into one compounded portfolio and compared
  against SPY buy & hold over the identical OOS window.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict

import pandas as pd
import yfinance as yf

from engine.backtest import Trade, run_portfolio
from engine.data import fetch_universe, fetch_intraday, _session
from engine.metrics import compute_metrics, spy_benchmark
from engine.optimize import prepare, evaluate, grid
from engine.strategy import Params, with_
from run_backtest import UNIVERSE, INTERVALS, RESULTS

MORNING = 11 * 60 + 30
FULL = 15 * 60 + 30
OOS_START = 25
FOLD_DAYS = 8
TOP_K = 3                 # trade the top-K train-ranked configs each fold (ensemble)
RISK_PER_TRADE = 1.5      # % of equity risked per trade in the OOS portfolio
LEVERAGE_CAP = 2.5
MAX_CONCURRENT = 6


def candidates() -> list[Params]:
    base = Params(timeframe="5m", momentum_mode="either", pullback_def="red_or_lh",
                  relvol_min=1.3, momentum_min_gain_atr=1.5,
                  stop_mode="pullback_low", trail_mode="none",
                  sizing_mode="risk", risk_per_trade_pct=1.0, pos_leverage_cap=2.0,
                  max_concurrent=4, in_play_filter=True, in_play_relvol=1.2)
    return list(dict.fromkeys(grid(base,
                                   entry_end_min=[MORNING, FULL],
                                   target_rr=[1.5, 2.0],
                                   in_play_gain_adr=[0.3, 0.5],
                                   timeframe=["5m", "15m"])))


def main() -> None:
    print("=== Loading data ===")
    data = fetch_universe(UNIVERSE, INTERVALS)
    spy_data = {iv: fetch_intraday("SPY", iv, 29 if iv == "1m" else 59) for iv in INTERVALS}
    enriched = prepare(data, {k: v for k, v in spy_data.items() if len(v) > 100})

    n_days = 1 + max(int(E["day"].max()) for (s, iv), E in enriched.items() if iv == "5m")
    print(f"  {n_days} trading days available")
    cands = candidates()
    print(f"  {len(cands)} candidate configs per fold")

    oos_trades: list[Trade] = []
    seen_keys: set = set()
    fold_log = []
    lo = OOS_START
    while lo < n_days:
        hi = min(lo + FOLD_DAYS, n_days)
        # rank candidates on train days [0, lo)
        ranked = sorted(((evaluate(enriched, p, part=(0, lo))["score"], k, p)
                         for k, p in enumerate(cands)), reverse=True)
        picked = [p for _, _, p in ranked[:TOP_K]]
        # trade the OOS block [lo, hi) with every picked config; dedupe overlapping
        # entries (same symbol+entry bar chosen by more than one config)
        fold_n, fold_rets = 0, []
        for p in picked:
            m_oos = evaluate(enriched, p, part=(lo, hi))
            tdf = m_oos.pop("_trades")
            m_oos.pop("_curve")
            for _, r in tdf.iterrows():
                key = (r.symbol, str(r.entry_time))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                oos_trades.append(Trade(r.symbol, r.entry_time, r.exit_time, r.entry,
                                        r.exit, r.ret_pct, r.bars_held, r.reason, r.risk_pct))
                fold_n += 1
                fold_rets.append(r.ret_pct)
        top = picked[0]
        fold_log.append({"fold": f"{lo}-{hi}", "top_tf": top.timeframe,
                         "top_rr": top.target_rr, "top_end": top.entry_end_min,
                         "top_gain_adr": top.in_play_gain_adr,
                         "train_score": ranked[0][0], "oos_n": fold_n,
                         "oos_avg_ret": float(pd.Series(fold_rets).mean()) if fold_rets else 0.0})
        print(f"  fold {lo:>2}-{hi:<2} top: tf={top.timeframe} rr={top.target_rr} "
              f"end={top.entry_end_min} gainADR={top.in_play_gain_adr} "
              f"(train {ranked[0][0]:+.2f}) -> OOS n={fold_n} "
              f"avg {fold_log[-1]['oos_avg_ret']:+.3f}%/trade")
        lo = hi

    print(f"\n=== Concatenated OOS portfolio (top-{TOP_K} ensemble, "
          f"risk {RISK_PER_TRADE}%/trade) ===")
    curve, tdf = run_portfolio(oos_trades, max_concurrent=MAX_CONCURRENT, sizing_mode="risk",
                               risk_per_trade_pct=RISK_PER_TRADE, pos_leverage_cap=LEVERAGE_CAP)
    m = compute_metrics(curve, tdf)
    for k in ["total_return_pct", "ann_return_pct", "n_trades", "win_rate",
              "profit_factor", "expectancy_pct", "max_dd_pct", "sharpe"]:
        print(f"  {k}: {m[k]:.2f}" if isinstance(m[k], float) else f"  {k}: {m[k]}")

    spy_raw = yf.download("SPY", period="90d", interval="1d", progress=False,
                          auto_adjust=True, session=_session())
    if isinstance(spy_raw.columns, pd.MultiIndex):
        spy_raw.columns = spy_raw.columns.get_level_values(0)
    spy_ret = spy_benchmark(spy_raw, curve.index[0], curve.index[-1]) if len(curve) else 0.0
    print(f"  SPY buy&hold over OOS window: {spy_ret:+.2f}%  "
          f"(edge: {m['total_return_pct'] - spy_ret:+.2f}pp)")

    pd.DataFrame(fold_log).to_csv(os.path.join(RESULTS, "walkforward_folds.csv"), index=False)
    tdf.to_csv(os.path.join(RESULTS, "walkforward_oos_trades.csv"), index=False)
    curve.to_csv(os.path.join(RESULTS, "walkforward_oos_equity.csv"))
    with open(os.path.join(RESULTS, "walkforward_summary.json"), "w", encoding="utf-8") as f:
        json.dump({"oos_metrics": {k: v for k, v in m.items() if isinstance(v, (int, float))},
                   "spy_oos_ret_pct": spy_ret, "folds": fold_log}, f, indent=2, default=str)
    print("  saved results/walkforward_*.csv/json")


if __name__ == "__main__":
    main()
