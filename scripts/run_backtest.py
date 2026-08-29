"""Run a single VCP backtest.

Usage:
  python scripts/run_backtest.py --config configs/base.yaml
  python scripts/run_backtest.py --config configs/base.yaml --start 2017-01-01 --end 2026-08-01
  python scripts/run_backtest.py --set risk.max_positions=5 --set exit.trail_ma=20
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vcp.config import load_config
from vcp.metrics import format_summary
from vcp.pipeline import DataCache, run_pipeline

ROOT = Path(__file__).resolve().parent.parent


def parse_sets(pairs: list[str]) -> dict:
    out: dict = {}
    for p in pairs:
        key, _, val = p.partition("=")
        node = out
        parts = key.split(".")
        for k in parts[:-1]:
            node = node.setdefault(k, {})
        try:
            node[parts[-1]] = json.loads(val)
        except json.JSONDecodeError:
            node[parts[-1]] = val
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--set", action="append", default=[], help="dot.key=value overrides")
    ap.add_argument("--out", default=None, help="write summary json + trades csv + equity csv here")
    args = ap.parse_args()

    overrides = parse_sets(args.set)
    if args.start:
        overrides.setdefault("backtest", {})["start"] = args.start
    if args.end:
        overrides.setdefault("backtest", {})["end"] = args.end
    cfg = load_config(args.config, overrides)

    result, summary = run_pipeline(cfg, DataCache())
    print(format_summary(summary))
    print(f"Setups detected   : {summary['n_setups']}  |  triggered: {summary['n_triggered']}"
          f"  |  universe: {summary['n_symbols']} symbols")

    if args.out:
        outdir = Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        with open(outdir / "summary.json", "w") as f:
            json.dump(summary, f, indent=2, default=str)
        result.equity.to_csv(outdir / "equity.csv")
        import pandas as pd
        pd.DataFrame([vars(t) for t in result.trades]).to_csv(outdir / "trades.csv", index=False)
        print(f"wrote results to {outdir}/")


if __name__ == "__main__":
    main()
