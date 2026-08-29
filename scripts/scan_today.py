"""Daily entry scanner: today's actionable VCP buy-stop candidates.

Two-phase flow:
  1. Detect pending setups on the full local dataset (bulk data may lag a few
     weeks; SPY is refreshed first so the calendar reaches the latest session).
  2. Refresh the candidate symbols from Yahoo, re-evaluate on current bars, and
     print/save the final ranked list with trigger, stop, and position size.

Usage:
  python scripts/scan_today.py --config configs/final.yaml --equity 100000
  python scripts/scan_today.py --no-refresh          # offline: bulk data only
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vcp.config import load_config
from vcp.pipeline import DataCache, build_artifacts
from vcp.refresh import refresh_spy, refresh_symbol_file
from vcp.scanner import scan

ROOT = Path(__file__).resolve().parent.parent

DISPLAY_COLS = ["symbol", "asof", "close", "trigger", "dist_to_trigger_pct",
                "stop", "stop_pct", "shares", "position_value", "n_contractions",
                "final_depth_pct", "vdu_ratio", "days_active_left", "rs_pct",
                "staleness_days"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "configs" / "final.yaml"))
    ap.add_argument("--equity", type=float, default=100_000.0,
                    help="account equity for position sizing")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--no-refresh", action="store_true",
                    help="skip Yahoo refresh (offline mode, bulk data only)")
    ap.add_argument("--refresh-universe", action="store_true",
                    help="refresh EVERY trend-template-passing symbol from Yahoo "
                         "(several minutes) so setups formed since the bulk-data "
                         "update are also found - the true daily mode")
    ap.add_argument("--max-dist", type=float, default=15.0,
                    help="hide candidates further than this %% below their trigger")
    args = ap.parse_args()

    cfg = load_config(args.config)
    today = pd.Timestamp.now().normalize()
    cfg.backtest.end = str(today.date())

    if not args.no_refresh:
        print("refreshing SPY benchmark from Yahoo ...", file=sys.stderr)
        refresh_spy()

    cache = DataCache()
    art = build_artifacts(cfg, cache)
    phase1 = scan(art, cfg, args.equity, max_staleness_days=40)
    phase1 = phase1[phase1.dist_to_trigger_pct.abs() <= args.max_dist]
    print(f"phase 1: {len(phase1)} pending candidates on bulk data", file=sys.stderr)

    result = phase1
    if not args.no_refresh:
        if args.refresh_universe:
            # every symbol currently passing trend template + liquidity at its
            # own last bar - so setups formed since the bulk update are found too
            syms = sorted({s for s in art["symbols"]
                           if art["tt_masks"][s][art["data"][s].last_idx]
                           and art["liq_masks"][s][art["data"][s].last_idx]}
                          | set(phase1.symbol))
        else:
            syms = phase1.symbol.tolist()
        if syms:
            print(f"refreshing {len(syms)} symbols from Yahoo "
                  f"(~{len(syms)*0.5/60:.0f} min) ...", file=sys.stderr)
            ok = 0
            for s in syms:
                ok += bool(refresh_symbol_file(s))
                time.sleep(0.4)                  # be polite to the API
            print(f"refreshed {ok}/{len(syms)}", file=sys.stderr)
            # rebuild on current bars; keep only refreshed, still-live candidates
            art = build_artifacts(cfg, DataCache())
            fresh = scan(art, cfg, args.equity, max_staleness_days=3)
            if fresh.empty:
                result = fresh
            else:
                result = fresh[fresh.symbol.isin(syms)
                               & (fresh.dist_to_trigger_pct.abs() <= args.max_dist)]
            print(f"phase 2: {len(result)} candidates live on current bars "
                  f"({len(phase1)} were pending on bulk data)", file=sys.stderr)

    market_on = bool(art["market"].regime_ok[-1])
    print(f"\n=== VCP entry candidates | as of {cfg.backtest.end} | "
          f"market regime: {'ON' if market_on else 'OFF - no new entries per strategy'} "
          f"| equity ${args.equity:,.0f} ===\n")
    if result.empty:
        print("no actionable candidates today.")
    else:
        print(result[DISPLAY_COLS].head(args.top).to_string(index=False))
        print(f"\norders: BUY STOP at 'trigger', initial stop at 'stop'; "
              f"skip fills more than {cfg.entry.max_chase_pct*100:.0f}% above the pivot; "
              f"confirm breakout-day volume >= {cfg.entry.bo_vol_mult:.1f}x its 50d average.")
    outdir = ROOT / "results" / "scans"
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"scan_{cfg.backtest.end}.csv"
    result.to_csv(out, index=False)
    print(f"\nsaved: {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
