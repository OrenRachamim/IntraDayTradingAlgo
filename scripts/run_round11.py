"""Round 11: weekly-timeframe VCP layer on top of the daily strategy.

Weekly bases (months long) are a structurally different setup universe with
longer moves and lower churn. Swept walk-forward, seeded from the round-5
winner; holdout untouched until the end.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.run_iterations as ri

ri.STAGES = [
    ("weekly", [
        {"weekly.enabled": True, "weekly.final_depth_max": fd,
         "weekly.min_contractions": mc, "vcp.setup_max_active_days": sad}
        for fd in (0.10, 0.15)
        for mc in (2, 3)
        for sad in (40, 60)
    ] + [
        {"weekly.enabled": False}     # control
    ]),
]

if __name__ == "__main__":
    sys.argv = ["run_round11", "--start-from", str(ri.RESULTS / "winner_round5.json")]
    ri.main()
