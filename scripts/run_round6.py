"""Round 6: targeted leverage-policy experiment (progressive exposure).

Round 5 found that unconditional 2:1 margin prints a 2.49x full-period multiple
but loses -36% on the 2022-2026 holdout. This round compares leverage policies
and the core-satellite (idle cash in SPY) variant on the three IS sub-windows,
then validates the best policy once on the untouched holdout.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_iterations import (FIELDS, FULL_END, FULL_START, IS_SUBWINDOWS,
                                    OOS_END, OOS_START, RESULTS, apply_overrides,
                                    run_one, score)
from vcp.config import load_config
from vcp.metrics import format_summary
from vcp.pipeline import DataCache, run_pipeline

ROOT = Path(__file__).resolve().parent.parent

VARIANTS = {
    "lev2_uncond":        {},   # round-5 winner as-is (control)
    "lev1":               {"risk.leverage": 1.0},
    "lev15_regime":       {"risk.leverage": 1.5, "risk.leverage_bear": 1.0},
    "lev2_regime":        {"risk.leverage_bear": 1.0},
    "lev2_regime_ec50":   {"risk.leverage_bear": 1.0, "risk.equity_curve_filter": 50},
    "lev1_spy":           {"risk.leverage": 1.0, "backtest.idle_cash_in_spy": True},
    "lev15_regime_spy":   {"risk.leverage": 1.5, "risk.leverage_bear": 1.0,
                           "backtest.idle_cash_in_spy": True},
    "lev2_regime_spy":    {"risk.leverage_bear": 1.0, "backtest.idle_cash_in_spy": True},
    "lev2_regime_ec50_spy": {"risk.leverage_bear": 1.0, "risk.equity_curve_filter": 50,
                             "backtest.idle_cash_in_spy": True},
}


def main() -> None:
    seed = json.load(open(RESULTS / "winner_round5.json"))
    base = load_config(ROOT / "configs" / "base.yaml")
    cache = DataCache()

    out_csv = RESULTS / "iterations.csv"
    f = open(out_csv, "a", newline="")
    writer = csv.DictWriter(f, fieldnames=FIELDS)

    results = {}
    for name, extra in VARIANTS.items():
        overrides = {**seed, **extra}
        rows = []
        for w_start, w_end in IS_SUBWINDOWS:
            cfg = apply_overrides(base, overrides)
            cfg.backtest.start, cfg.backtest.end = w_start, w_end
            cfg.name = name
            rows.append(run_one(cfg, cache, writer, "round6", name,
                                f"IS{w_start[:4]}", overrides))
        results[name] = (score(rows), rows, overrides)
        f.flush()

    best_name = max(results, key=lambda k: results[k][0])
    print(f"\n==== round-6 best policy: {best_name} "
          f"(score={results[best_name][0]:.4f}) ====")
    best_overrides = results[best_name][2]

    for window, (w_start, w_end) in (("OOS", (OOS_START, OOS_END)),
                                     ("FULL", (FULL_START, FULL_END))):
        cfg = apply_overrides(base, best_overrides)
        cfg.name = f"round6_{best_name}_{window}"
        cfg.backtest.start, cfg.backtest.end = w_start, w_end
        result, s = run_pipeline(cfg, cache)
        run_one(cfg, cache, writer, "round6_val", f"{best_name}_{window}",
                window, best_overrides)
        print(f"\n---- {best_name} on {window} ----")
        print(format_summary(s))
        outdir = RESULTS / f"round6_{window}"
        outdir.mkdir(exist_ok=True)
        with open(outdir / "summary.json", "w") as fh:
            json.dump(s, fh, indent=2, default=str)
        result.equity.to_csv(outdir / "equity.csv")
        import pandas as pd
        pd.DataFrame([vars(t) for t in result.trades]).to_csv(outdir / "trades.csv", index=False)

    with open(RESULTS / "winner_round6.json", "w") as fh:
        json.dump(best_overrides, fh, indent=2)
    f.close()


if __name__ == "__main__":
    main()
