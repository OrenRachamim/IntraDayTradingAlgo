"""Round 9: profitability-doubling sweep, driven by the setup-outcome analysis.

Stages (walk-forward on the 3 regime windows, holdout untouched until the end):
  filter  - structure-quality gates found in results/setup_analysis.csv
            (4+ contractions, real opening correction, pivot off the highs)
  rank    - candidate ranking: tightness (current) vs RS vs structure quality
  stops2  - ATR-scaled stops vs fixed 7%
  exits2  - target/trail interplay with a dead-money time stop
  cash    - idle-cash SPY parking, re-tested under the tighter filter

Reuses the run_iterations engine with a custom stage list, seeded from the
round-5 winner.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.run_iterations as ri

ri.STAGES = [
    ("filter", [
        {"vcp.min_contractions": mc, "vcp.base_first_depth_min": fd,
         "vcp.pivot_min_below_base_high": pm}
        for mc in (3, 4)
        for fd in (0.0, 0.10)
        for pm in (0.0, 0.02)
    ]),
    ("rank", [
        {"entry.rank_by": rb}
        for rb in ("tightness", "rs", "contractions")
    ]),
    ("stops2", [
        {"risk.stop_atr_mult": am, "risk.stop_use_contraction_low": scl}
        for am in (0.0, 1.5, 2.5)
        for scl in (True, False)
    ]),
    ("exits2", [
        {"exit.target_R": tr, "exit.trail_ma": tm, "exit.time_stop_days": ts}
        for tr in (0.0, 6.0)
        for tm in (50, 65)
        for ts in (0, 30)
    ]),
    ("cash", [
        {"backtest.idle_cash_in_spy": spy}
        for spy in (False, True)
    ]),
]

if __name__ == "__main__":
    sys.argv = ["run_round9", "--start-from", str(ri.RESULTS / "winner_round5.json")]
    ri.main()
