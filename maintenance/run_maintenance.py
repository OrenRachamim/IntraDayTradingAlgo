#!/usr/bin/env python3
"""Weekly automated maintenance for the live Micro Pullback system.

Run from cron every Sunday. Steps:

  1. Refresh the volume-filtered universe cache (membership + liquidity).
  2. Pull fresh Yahoo intraday data (1m/5m/15m/30m, cache-busting).
  3. Re-run the strict walk-forward evaluation on the fresh data.
  4. Re-run the scanner sweep on the fresh 1m window.
  5. Drift check: live trade log (SQLite) vs backtest expectations.
  6. Health verdict -> keep or auto-disable the 'trading_enabled' flag.
  7. Write a dated markdown report to reports/ and send a Telegram summary.

The engine never trades while trading_enabled != '1', so a failed health check
fails safe. Re-enable manually with:
  python -m maintenance.run_maintenance --enable
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.universe import build_universe, CACHE_DIR          # noqa: E402
from live.config import load_config, REPORTS_DIR               # noqa: E402
from live.notify import notify, get_logger                     # noqa: E402
from live import state                                         # noqa: E402

RESULTS = os.path.join(ROOT, "results")


def run_script(name: str, timeout_s: int = 3600) -> tuple[bool, str]:
    log = get_logger()
    log.info(f"maintenance: running {name}")
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, name)],
                           capture_output=True, text=True, timeout=timeout_s, cwd=ROOT)
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-25:])
        return r.returncode == 0, tail
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"


def main() -> None:
    cfg = load_config()
    log = get_logger()

    if "--enable" in sys.argv:
        state.set_flag("trading_enabled", "1", "manually re-enabled")
        notify(cfg, "✅ trading re-enabled manually", important=True)
        return
    if "--disable" in sys.argv:
        state.set_flag("trading_enabled", "0", "manually disabled")
        notify(cfg, "🛑 trading disabled manually", important=True)
        return

    stamp = datetime.now().strftime("%Y-%m-%d")
    report: list[str] = [f"# Maintenance report {stamp}\n"]
    healthy = True
    reasons: list[str] = []

    # 1+2: refresh universe & data (drop caches so everything re-downloads)
    log.info("maintenance: refreshing caches")
    if os.path.isdir(CACHE_DIR):
        shutil.rmtree(CACHE_DIR)
    try:
        uni = build_universe(min_dollar_vol_m=150.0, min_price=5.0, top_n=150,
                             use_cache=False)
        report.append(f"- universe refreshed: {len(uni)} symbols")
    except Exception as e:  # noqa: BLE001
        healthy = False
        reasons.append(f"universe refresh failed: {e}")
        report.append(f"- **universe refresh FAILED**: {e}")

    # 3: walk-forward on fresh data
    ok, tail = run_script("run_walkforward.py")
    report.append("\n## Walk-forward (fresh data)\n```\n" + tail + "\n```")
    wf_pf = 0.0
    wf_path = os.path.join(RESULTS, "walkforward_summary.json")
    if ok and os.path.exists(wf_path):
        wf = json.load(open(wf_path, encoding="utf-8"))
        wf_pf = wf.get("oos_metrics", {}).get("profit_factor", 0.0)
        wf_ret = wf.get("oos_metrics", {}).get("total_return_pct", 0.0)
        report.append(f"\nOOS profit factor: **{wf_pf:.2f}**, return {wf_ret:+.2f}%")
        if wf_pf < cfg.maint_wf_pf_floor:
            healthy = False
            reasons.append(f"walk-forward PF {wf_pf:.2f} < floor {cfg.maint_wf_pf_floor}")
    else:
        healthy = False
        reasons.append("walk-forward run failed")

    # 4: scanner sweep on fresh 1m data
    ok2, tail2 = run_script("run_scanner_backtest.py")
    report.append("\n## Scanner sweep (fresh data)\n```\n" + tail2 + "\n```")
    if not ok2:
        healthy = False
        reasons.append("scanner backtest failed")

    # 5: live drift check
    live = state.live_trade_stats()
    report.append("\n## Live drift check\n")
    if live.get("n", 0) >= cfg.maint_min_live_trades:
        report.append(f"- live trades: {live['n']}, PF {live['pf']:.2f}, "
                      f"win rate {live['win_rate']:.0f}%, "
                      f"total PnL ${live['total_pnl_usd']:,.0f}")
        if live["pf"] < cfg.maint_live_pf_floor:
            healthy = False
            reasons.append(f"live PF {live['pf']:.2f} < floor {cfg.maint_live_pf_floor}")
    else:
        report.append(f"- only {live.get('n', 0)} live trades "
                      f"(< {cfg.maint_min_live_trades}) — drift check skipped")

    # 6: verdict
    if healthy:
        state.set_flag("trading_enabled", "1", f"maintenance {stamp}: healthy")
        verdict = "✅ HEALTHY — trading enabled"
    else:
        state.set_flag("trading_enabled", "0",
                       f"maintenance {stamp}: " + "; ".join(reasons))
        verdict = "🛑 UNHEALTHY — trading auto-disabled: " + "; ".join(reasons)
    report.append(f"\n## Verdict\n\n{verdict}\n")

    # 7: persist + notify
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, f"maintenance_{stamp}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    log.info(f"maintenance report: {path}")
    notify(cfg, f"🔧 weekly maintenance {stamp}: {verdict}\n"
                f"walk-forward PF {wf_pf:.2f} | live trades {live.get('n', 0)}",
           important=True)


if __name__ == "__main__":
    main()
