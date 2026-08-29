"""Round 10: pyramiding sweep (scale into winners), seeded from the round-5 winner.

The add-on is bounded by max_weight, so pyramid variants are also tested with a
higher per-position cap - otherwise a winner already at the cap has no room.
Walk-forward selection as usual; holdout untouched until the end.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.run_iterations as ri

ri.STAGES = [
    ("pyramid", [
        {"risk.pyramid_at_R": ar, "risk.pyramid_frac": fr, "risk.max_weight": mw}
        for ar in (1.0, 2.0)
        for fr in (0.5, 1.0)
        for mw in (0.35, 0.50)
    ] + [
        {"risk.max_weight": 0.50}     # higher cap WITHOUT pyramid, as its own control
    ]),
]

if __name__ == "__main__":
    sys.argv = ["run_round10", "--start-from", str(ri.RESULTS / "winner_round5.json")]
    ri.main()
