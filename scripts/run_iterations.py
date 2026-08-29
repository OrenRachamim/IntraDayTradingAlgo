"""Configuration iteration engine.

Greedy stage-wise search (coordinate descent over semantic parameter groups),
optimizing the IN-SAMPLE window only; the winning config is then validated
untouched on the OUT-OF-SAMPLE window and the full period.

Every run is appended to results/iterations.csv. All candidate values stay
inside Minervini's documented ranges (docs/VCP_RESEARCH.md §9) — the search
tunes within the philosophy rather than mutating into something else.

Usage:
  python scripts/run_iterations.py                 # full staged search
  python scripts/run_iterations.py --quick         # smaller grid (debug)
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vcp.config import Config, load_config
from vcp.metrics import format_summary
from vcp.pipeline import DataCache, run_pipeline

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"

IS_START, IS_END = "2004-01-01", "2017-01-01"
OOS_START, OOS_END = "2017-01-01", "2026-08-01"
FULL_START, FULL_END = "2004-01-01", "2026-08-01"

# Parameter groups swept one stage at a time; each stage keeps the best combo.
STAGES: list[tuple[str, list[dict]]] = [
    ("exposure", [
        {"risk.max_positions": mp, "risk.risk_per_trade": rpt, "risk.max_weight": mw}
        for mp in (5, 8, 12)
        for rpt in (0.01, 0.02)
        for mw in (0.15, 0.25)
    ]),
    ("exits", [
        {"exit.target_R": tr, "exit.trail_ma": tm, "exit.breakeven_at_R": be}
        for tr in (0.0, 2.0, 3.0)
        for tm in (20, 50)
        for be in (0.0, 1.0)
    ]),
    ("screen", [
        {"tt.rs_percentile_min": rs, "universe.min_dollar_volume": dv}
        for rs in (70.0, 80.0, 90.0)
        for dv in (2e6, 10e6)
    ]),
    ("vcp_shape", [
        {"vcp.contraction_ratio_max": cr, "vcp.final_depth_max": fd,
         "vcp.vdu_ratio_max": vdu, "vcp.min_contractions": mc}
        for cr in (0.6, 0.75, 0.9)
        for fd in (0.08, 0.12)
        for vdu in (0.7, 0.85, 1.2)
        for mc in (2, 3)
    ]),
    ("stops", [
        {"risk.stop_pct": sp, "risk.stop_use_contraction_low": scl}
        for sp in (0.04, 0.06, 0.08)
        for scl in (True, False)
    ]),
    ("market", [
        {"entry.market_filter": mf, "entry.market_filter_ma": ma,
         "vcp.setup_max_active_days": sad}
        for mf, ma in ((True, 200), (True, 100), (False, 200))
        for sad in (20, 40)
    ]),
]

FIELDS = ["stage", "run", "window", "overrides", "total_return", "cagr", "sharpe",
          "max_drawdown", "profit_multiple_vs_spy", "n_trades", "win_rate",
          "avg_R", "profit_factor", "avg_exposure", "n_triggered", "elapsed_s"]


def apply_overrides(cfg: Config, overrides: dict) -> Config:
    cfg = copy.deepcopy(cfg)
    for key, val in overrides.items():
        node = cfg
        parts = key.split(".")
        for p in parts[:-1]:
            node = getattr(node, p)
        setattr(node, parts[-1], val)
    return cfg


def run_one(cfg: Config, cache: DataCache, writer, stage: str, run_name: str,
            window: str, overrides: dict) -> dict:
    t0 = time.time()
    _, s = run_pipeline(cfg, cache)
    row = {
        "stage": stage, "run": run_name, "window": window,
        "overrides": json.dumps(overrides, sort_keys=True),
        "total_return": round(s["total_return"], 4),
        "cagr": round(s["cagr"], 4),
        "sharpe": round(s["sharpe"], 3),
        "max_drawdown": round(s["max_drawdown"], 4),
        "profit_multiple_vs_spy": round(s["profit_multiple_vs_spy"], 3),
        "n_trades": s.get("n_trades", 0),
        "win_rate": round(s.get("win_rate", float("nan")), 4),
        "avg_R": round(s.get("avg_R", float("nan")), 3),
        "profit_factor": round(s.get("profit_factor", float("nan")), 3),
        "avg_exposure": round(s["avg_exposure"], 4),
        "n_triggered": s["n_triggered"],
        "elapsed_s": round(time.time() - t0, 1),
    }
    writer.writerow(row)
    print(f"[{stage:>9}] {run_name:<40} mult={row['profit_multiple_vs_spy']:>7.2f}x "
          f"cagr={row['cagr']*100:6.2f}% dd={row['max_drawdown']*100:6.1f}% "
          f"sharpe={row['sharpe']:5.2f} trades={row['n_trades']}", flush=True)
    return row


def score(row: dict) -> float:
    """IS selection score: profit multiple, penalized for pathologies.

    Guards keep the search honest: enough trades to mean something, and
    drawdowns no worse than buy-and-holding the index through 2008 (~-55%).
    """
    if row["n_trades"] < 100:
        return -1e9
    if row["max_drawdown"] < -0.55:
        return -1e9
    return row["profit_multiple_vs_spy"] + 0.1 * row["sharpe"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()

    RESULTS.mkdir(exist_ok=True)
    out_csv = RESULTS / "iterations.csv"
    fresh = not out_csv.exists()
    f = open(out_csv, "a", newline="")
    writer = csv.DictWriter(f, fieldnames=FIELDS)
    if fresh:
        writer.writeheader()

    cache = DataCache()
    base = load_config(ROOT / "configs" / "base.yaml")
    base.backtest.start, base.backtest.end = IS_START, IS_END

    best_overrides: dict = {}
    base_row = run_one(base, cache, writer, "baseline", "base", "IS", {})
    best_row = base_row

    stages = STAGES if not args.quick else [(n, g[:4]) for n, g in STAGES]
    for stage_name, grid in stages:
        stage_best_row, stage_best_ov = best_row, None
        for i, group_ov in enumerate(grid):
            overrides = {**best_overrides, **group_ov}
            if overrides == best_overrides:
                continue
            cfg = apply_overrides(base, overrides)
            cfg.name = f"{stage_name}_{i}"
            row = run_one(cfg, cache, writer, stage_name, json.dumps(group_ov)[:60],
                          "IS", overrides)
            if score(row) > score(stage_best_row):
                stage_best_row, stage_best_ov = row, group_ov
        if stage_best_ov is not None:
            best_overrides.update(stage_best_ov)
            best_row = stage_best_row
        print(f"== stage {stage_name} best: {json.dumps(best_overrides)} "
              f"(mult={best_row['profit_multiple_vs_spy']}x)", flush=True)
        f.flush()

    print("\n==== WINNER (in-sample) ====")
    print(json.dumps(best_overrides, indent=2))

    # ---- validation: untouched OOS window + full period ----
    for window, (w_start, w_end) in (("OOS", (OOS_START, OOS_END)),
                                     ("FULL", (FULL_START, FULL_END))):
        cfg = apply_overrides(base, best_overrides)
        cfg.name = f"winner_{window}"
        cfg.backtest.start, cfg.backtest.end = w_start, w_end
        result, s = run_pipeline(cfg, cache)
        run_one(cfg, cache, writer, "validate", f"winner_{window}", window, best_overrides)
        print(f"\n---- winner on {window} ({w_start} -> {w_end}) ----")
        print(format_summary(s))
        outdir = RESULTS / f"winner_{window}"
        outdir.mkdir(exist_ok=True)
        with open(outdir / "summary.json", "w") as fh:
            json.dump(s, fh, indent=2, default=str)
        result.equity.to_csv(outdir / "equity.csv")
        import pandas as pd
        pd.DataFrame([vars(t) for t in result.trades]).to_csv(outdir / "trades.csv", index=False)

    with open(RESULTS / "winner_overrides.json", "w") as fh:
        json.dump(best_overrides, fh, indent=2)
    f.close()


if __name__ == "__main__":
    main()
