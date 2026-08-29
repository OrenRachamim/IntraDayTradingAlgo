"""Round 8: failed-breakout exit experiment.

The 2022+ holdout showed a negative raw trade edge (PF 0.85, 21% win rate) -
the modern 'breakout-fakeout' regime. This round tests the fast failed-breakout
exit (close back under the pivot on entry day -> exit next open) across
leverage policies, on the IS windows; best policy validates once on the holdout.
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

FAIL = {"exit.fail_close_below_pivot": True}
VARIANTS = {
    "ctrl_lev2":       {},
    "fail_lev2":       {**FAIL},
    "fail_lev2_spy":   {**FAIL, "backtest.idle_cash_in_spy": True},
    "fail_lev15":      {**FAIL, "risk.leverage": 1.5},
    "fail_lev15_spy":  {**FAIL, "risk.leverage": 1.5, "backtest.idle_cash_in_spy": True},
    "fail_lev1":       {**FAIL, "risk.leverage": 1.0},
    "fail_lev1_spy":   {**FAIL, "risk.leverage": 1.0, "backtest.idle_cash_in_spy": True},
}


def main() -> None:
    seed = json.load(open(RESULTS / "winner_round5.json"))
    base = load_config(ROOT / "configs" / "base.yaml")
    cache = DataCache()

    f = open(RESULTS / "iterations.csv", "a", newline="")
    writer = csv.DictWriter(f, fieldnames=FIELDS)

    results = {}
    for name, extra in VARIANTS.items():
        overrides = {**seed, **extra}
        rows = []
        for w_start, w_end in IS_SUBWINDOWS:
            cfg = apply_overrides(base, overrides)
            cfg.backtest.start, cfg.backtest.end = w_start, w_end
            cfg.name = name
            rows.append(run_one(cfg, cache, writer, "round8", name,
                                f"IS{w_start[:4]}", overrides))
        results[name] = (score(rows), rows, overrides)
        f.flush()

    best_name = max(results, key=lambda k: results[k][0])
    print(f"\n==== round-8 best policy: {best_name} "
          f"(score={results[best_name][0]:.4f}) ====")
    best_overrides = results[best_name][2]

    for window, (w_start, w_end) in (("OOS", (OOS_START, OOS_END)),
                                     ("FULL", (FULL_START, FULL_END))):
        cfg = apply_overrides(base, best_overrides)
        cfg.name = f"round8_{best_name}_{window}"
        cfg.backtest.start, cfg.backtest.end = w_start, w_end
        result, s = run_pipeline(cfg, cache)
        run_one(cfg, cache, writer, "round8_val", f"{best_name}_{window}",
                window, best_overrides)
        print(f"\n---- {best_name} on {window} ----")
        print(format_summary(s))
        outdir = RESULTS / f"round8_{window}"
        outdir.mkdir(exist_ok=True)
        with open(outdir / "summary.json", "w") as fh:
            json.dump(s, fh, indent=2, default=str)
        result.equity.to_csv(outdir / "equity.csv")
        import pandas as pd
        pd.DataFrame([vars(t) for t in result.trades]).to_csv(outdir / "trades.csv", index=False)

    with open(RESULTS / "winner_round8.json", "w") as fh:
        json.dump(best_overrides, fh, indent=2)
    f.close()


if __name__ == "__main__":
    main()
