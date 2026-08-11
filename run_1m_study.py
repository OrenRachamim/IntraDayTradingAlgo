#!/usr/bin/env python3
"""Comprehensive 1-minute timeframe study for the Micro Pullback strategy.

Yahoo provides ~29 calendar days (~20 trading days) of 1m bars, so every result
here covers that window only. Stages:

  A. Structure scan  – momentum mode x pullback def x entry window x stop mode
                       x target R x trailing (in-play filter on, doctrine values)
  B. Filter scan     – RSI / MACD / bar relvol / in-play variants around the
                       stage-A leaders
  C. Cost sensitivity – best configs at 0 / 3 / 6 bps per round trip
  D. Robustness      – train/validation day split (70/30) + execution-model
                       bounds (pessimistic vs optimistic) for the leaders
  E. Benchmarks      – SPY buy & hold and the production 5m+15m ensemble over
                       the identical 1m window
"""
from __future__ import annotations

import os
from dataclasses import asdict

import pandas as pd
import yfinance as yf

import engine.backtest as bt
from engine.data import fetch_universe, fetch_intraday, _session
from engine.metrics import spy_benchmark
from engine.optimize import prepare, evaluate, grid
from engine.strategy import Params, with_
from run_backtest import UNIVERSE, INTERVALS, RESULTS, params_from_row
from run_final_report import FINAL_CONFIGS, run_ensemble

MORNING = 11 * 60 + 30
FULL = 15 * 60 + 30

BASE = Params(timeframe="1m", momentum_mode="either", pullback_def="red_or_lh",
              relvol_min=1.3, momentum_min_gain_atr=1.5, stop_mode="pullback_low",
              trail_mode="none", target_rr=1.5, entry_end_min=MORNING,
              in_play_filter=True, in_play_gain_adr=0.3, in_play_relvol=1.2,
              sizing_mode="risk", risk_per_trade_pct=1.0, pos_leverage_cap=2.0,
              max_concurrent=4)

SHOW = ["total_return_pct", "profit_factor", "win_rate", "n_trades", "expectancy_pct",
        "max_dd_pct", "avg_bars_held"]


def stage_a() -> list[Params]:
    return list(dict.fromkeys(
        grid(BASE,
             momentum_mode=["surge", "either"],
             pullback_def=["lower_high", "red_or_lh"],
             entry_end_min=[MORNING, FULL],
             stop_mode=["pullback_low", "atr", "pct"],
             target_rr=[1.0, 1.5, 2.0, 3.0],
             trail_mode=["none", "pct"])))


def stage_b(leaders: list[Params]) -> list[Params]:
    out = []
    for b in leaders:
        out += grid(b,
                    rsi_filter=[False, True],
                    macd_filter=[False, True],
                    relvol_min=[1.0, 1.3, 1.7],
                    max_pullback_bars=[2, 3])
        out += [with_(b, in_play_filter=False),
                with_(b, in_play_gain_adr=0.5, in_play_relvol=1.5),
                with_(b, market_filter=True)]
    return list(dict.fromkeys(out))


def run_stage(enriched, params_list, tag, part=None) -> pd.DataFrame:
    rows = []
    for k, p in enumerate(params_list):
        m = evaluate(enriched, p, part=part)
        m.pop("_curve"), m.pop("_trades")
        m.update(asdict(p))
        rows.append(m)
        if (k + 1) % 40 == 0:
            print(f"    [{tag}] {k + 1}/{len(params_list)}")
    df = pd.DataFrame(rows).sort_values("total_return_pct", ascending=False).reset_index(drop=True)
    best = df.iloc[0]
    print(f"  [{tag}] {len(df)} configs | best: ret {best['total_return_pct']:+.2f}% "
          f"pf {best['profit_factor']:.2f} n={best['n_trades']}")
    return df


def main() -> None:
    print("=== Loading data ===")
    data = fetch_universe(UNIVERSE, INTERVALS)
    spy_data = {iv: fetch_intraday("SPY", iv, 29 if iv == "1m" else 59) for iv in INTERVALS}
    enriched = prepare(data, {k: v for k, v in spy_data.items() if len(v) > 100})
    e1m = {k: v for k, v in enriched.items() if k[1] == "1m"}
    n_days = 1 + max(int(E["day"].max()) for E in e1m.values())
    win_start = min(E["index"][0] for E in e1m.values())
    win_end = max(E["index"][-1] for E in e1m.values())
    print(f"  1m window: {win_start:%Y-%m-%d} -> {win_end:%Y-%m-%d} ({n_days} trading days)")

    print("\n=== Stage A: structure scan ===")
    dfa = run_stage(enriched, stage_a(), "A")
    dfa.to_csv(os.path.join(RESULTS, "study1m_stageA.csv"), index=False)

    leaders = [params_from_row(r) for _, r in
               dfa.drop_duplicates(subset=["momentum_mode", "pullback_def", "entry_end_min",
                                           "stop_mode"]).head(4).iterrows()]

    print("\n=== Stage B: filter scan around leaders ===")
    dfb = run_stage(enriched, stage_b(leaders), "B")
    dfb.to_csv(os.path.join(RESULTS, "study1m_stageB.csv"), index=False)

    top = [params_from_row(r) for _, r in dfb.head(5).iterrows()]

    print("\n=== Stage C: cost sensitivity (best 3 configs) ===")
    orig_cost = bt.COST_BPS_PER_SIDE
    for p in top[:3]:
        line = []
        for side_bps in [0.0, 1.5, 3.0]:
            bt.COST_BPS_PER_SIDE = side_bps
            m = evaluate(enriched, p)
            line.append(f"{2 * side_bps:.0f}bps: {m['total_return_pct']:+.2f}% "
                        f"(pf {m['profit_factor']:.2f})")
        bt.COST_BPS_PER_SIDE = orig_cost
        print(f"  rr={p.target_rr} stop={p.stop_mode} trail={p.trail_mode} "
              f"end={p.entry_end_min} | " + " | ".join(line))

    print("\n=== Stage D: robustness of the top-5 ===")
    rows = []
    for p in top:
        tr = evaluate(enriched, p, part="train")
        va = evaluate(enriched, p, part="validation")
        po = evaluate(enriched, with_(p, intrabar="optimistic"))
        rows.append({**{k: getattr(p, k) for k in ("stop_mode", "target_rr", "trail_mode",
                                                   "entry_end_min", "relvol_min",
                                                   "rsi_filter", "macd_filter")},
                     "full_ret": evaluate(enriched, p)["total_return_pct"],
                     "train_ret": tr["total_return_pct"], "train_pf": tr["profit_factor"],
                     "val_ret": va["total_return_pct"], "val_pf": va["profit_factor"],
                     "optimistic_ret": po["total_return_pct"]})
        r = rows[-1]
        print(f"  rr={r['target_rr']} stop={r['stop_mode']} trail={r['trail_mode']}: "
              f"full {r['full_ret']:+.2f}% | train {r['train_ret']:+.2f}% "
              f"(pf {r['train_pf']:.2f}) | val {r['val_ret']:+.2f}% (pf {r['val_pf']:.2f}) | "
              f"optimistic bound {r['optimistic_ret']:+.2f}%")
    pd.DataFrame(rows).to_csv(os.path.join(RESULTS, "study1m_robustness.csv"), index=False)

    print("\n=== Stage E: benchmarks over the same 1m window ===")
    spy_raw = yf.download("SPY", period="90d", interval="1d", progress=False,
                          auto_adjust=True, session=_session())
    if isinstance(spy_raw.columns, pd.MultiIndex):
        spy_raw.columns = spy_raw.columns.get_level_values(0)
    spy_ret = spy_benchmark(spy_raw, win_start, win_end)
    print(f"  SPY buy & hold: {spy_ret:+.2f}%")
    # production 5m+15m ensemble restricted to the same days
    day0 = None
    for (sym, iv), E in enriched.items():
        if iv == "5m":
            idx = E["index"]
            mask = idx >= win_start
            if mask.any():
                day0 = int(E["day"][mask.argmax()])
                break
    m, _, _ = run_ensemble(enriched, part=(day0, 10_000))
    print(f"  5m+15m production ensemble: {m['total_return_pct']:+.2f}% "
          f"(pf {m['profit_factor']:.2f}, n={m['n_trades']})")
    best = dfb.iloc[0]
    print(f"  best 1m config:             {best['total_return_pct']:+.2f}% "
          f"(pf {best['profit_factor']:.2f}, n={best['n_trades']})")


if __name__ == "__main__":
    main()
