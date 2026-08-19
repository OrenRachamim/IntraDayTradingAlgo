#!/usr/bin/env python3
"""Focused robust scan around the in-play Micro Pullback region.

Every config is evaluated on train AND validation; a config is ROBUST when both
sides are profitable with PF > 1 and enough trades. Robust survivors are combined
into an equal-risk ensemble, evaluated on train/validation/full, and reported
against SPY buy & hold.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict

import pandas as pd
import yfinance as yf

from engine.backtest import simulate_symbol, run_portfolio
from engine.data import fetch_universe, fetch_intraday, _session
from engine.metrics import compute_metrics, spy_benchmark
from engine.optimize import prepare, evaluate, _split_enriched, grid
from engine.strategy import Params, with_
from run_backtest import UNIVERSE, INTERVALS, RESULTS

MORNING = 11 * 60 + 30
FULL = 15 * 60 + 30


def candidates() -> list[Params]:
    base = Params(momentum_mode="either", pullback_def="red_or_lh",
                  stop_mode="pullback_low", trail_mode="none",
                  sizing_mode="risk", risk_per_trade_pct=1.0, pos_leverage_cap=2.0,
                  max_concurrent=4, in_play_filter=True, in_play_relvol=1.2)
    out = []
    for tf in ["5m", "15m"]:
        out += grid(with_(base, timeframe=tf),
                    entry_end_min=[MORNING, FULL],
                    relvol_min=[1.0, 1.3],
                    momentum_min_gain_atr=[1.0, 1.5],
                    in_play_gain_adr=[0.3, 0.4, 0.5],
                    target_rr=[1.2, 1.5],
                    trail_mode=["none", "pct"])
    return list(dict.fromkeys(out))


def ensemble(enriched: dict, configs: list[Params], part: str | None):
    all_trades, k = [], max(len(configs), 1)
    for p in configs:
        for (sym, iv), E in enriched.items():
            if iv != p.timeframe:
                continue
            if part:
                cut = int(E["day"].max() * 0.7) + 1
                E = _split_enriched(E, cut, part)
                if len(E["open"]) < 100:
                    continue
            all_trades.extend(simulate_symbol(sym, E, p))
    curve, tdf = run_portfolio(all_trades, max_concurrent=6, sizing_mode="risk",
                               risk_per_trade_pct=max(1.5 / k, 0.35), pos_leverage_cap=1.5)
    return compute_metrics(curve, tdf), curve, tdf


def main() -> None:
    print("=== Loading data ===")
    data = fetch_universe(UNIVERSE, INTERVALS)
    spy_data = {iv: fetch_intraday("SPY", iv, 29 if iv == "1m" else 59) for iv in INTERVALS}
    enriched = prepare(data, {k: v for k, v in spy_data.items() if len(v) > 100})

    print("\n=== Robust scan (train AND validation must be profitable) ===")
    rows, robust = [], []
    cands = candidates()
    for k, p in enumerate(cands):
        tr = evaluate(enriched, p, part="train")
        va = evaluate(enriched, p, part="validation")
        ok = (tr["total_return_pct"] > 0 and va["total_return_pct"] > 0
              and tr["profit_factor"] > 1.05 and va["profit_factor"] > 1.05
              and tr["n_trades"] >= 25 and va["n_trades"] >= 8)
        rows.append({**asdict(p), "train_ret": tr["total_return_pct"],
                     "train_pf": tr["profit_factor"], "train_n": tr["n_trades"],
                     "val_ret": va["total_return_pct"], "val_pf": va["profit_factor"],
                     "val_n": va["n_trades"], "robust": ok,
                     "combo_score": min(tr["total_return_pct"], va["total_return_pct"] * 2)})
        if ok:
            robust.append(p)
        if (k + 1) % 25 == 0:
            print(f"  {k + 1}/{len(cands)} scanned, robust so far: {len(robust)}")
    df = pd.DataFrame(rows).sort_values(["robust", "combo_score"], ascending=False)
    df.to_csv(os.path.join(RESULTS, "inplay_scan.csv"), index=False)
    print(f"  robust configs: {len(robust)}/{len(cands)}")
    show = ["timeframe", "entry_end_min", "relvol_min", "momentum_min_gain_atr",
            "in_play_gain_adr", "target_rr", "trail_mode",
            "train_ret", "train_pf", "train_n", "val_ret", "val_pf", "val_n"]
    print(df[df["robust"]][show].head(15).round(2).to_string())

    if not robust:
        print("no robust configs — stopping before ensemble")
        return
    # diversity: best per (timeframe, entry window, trail) family, cap at 6
    fam = df[df["robust"]].drop_duplicates(subset=["timeframe", "entry_end_min", "trail_mode"])
    from run_backtest import params_from_row
    picked = [params_from_row(r) for _, r in fam.head(6).iterrows()]

    print(f"\n=== Ensemble of {len(picked)} diverse robust configs ===")
    for part in ("train", "validation", None):
        m, curve, tdf = ensemble(enriched, picked, part)
        print(f"  {part or 'FULL':>10}: ret {m['total_return_pct']:+.2f}%  "
              f"pf {m['profit_factor']:.2f}  n={m['n_trades']}  dd {m['max_dd_pct']:.2f}%  "
              f"sharpe {m['sharpe']:.2f}  wr {m['win_rate']:.0f}%")
        if part is None:
            tdf.to_csv(os.path.join(RESULTS, "ensemble_trades.csv"), index=False)
            curve.to_csv(os.path.join(RESULTS, "ensemble_equity_curve.csv"))
            spy_raw = yf.download("SPY", period="90d", interval="1d", progress=False,
                                  auto_adjust=True, session=_session())
            if isinstance(spy_raw.columns, pd.MultiIndex):
                spy_raw.columns = spy_raw.columns.get_level_values(0)
            spy_ret = spy_benchmark(spy_raw, curve.index[0], curve.index[-1])
            print(f"  SPY buy&hold same window: {spy_ret:+.2f}%  "
                  f"(strategy edge: {m['total_return_pct'] - spy_ret:+.2f}pp)")
            with open(os.path.join(RESULTS, "ensemble_configs.json"), "w", encoding="utf-8") as f:
                json.dump([asdict(p) for p in picked], f, indent=2)


if __name__ == "__main__":
    main()
