"""Daily morning scanner: pick the stocks that are IN PLAY *today*.

Static symbol features (ADR, gap frequency, volume volatility, dollar volume)
turned out NOT to separate winners from losers (see
results/scanner_feature_analysis.csv). What matters is the day itself, so the
scanner works like a trader's 9:59 watchlist build:

  At 10:00 ET each session, for every candidate symbol compute
    gap        = today's open / yesterday's close - 1
    early_move = price at 10:00 / today's open - 1
    early_rv   = cumulative volume so far vs usual for this time of day
  Eligible if gap >= gap_min OR (early_move >= move_min AND early_rv >= rv_min).
  Rank eligible symbols by score = max(gap,0) + max(early_move,0) + 0.1*early_rv
  and keep the top K. Trades are allowed only on selected symbols, only after
  10:00 — everything uses information available at selection time.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SELECT_MINUTE = 10 * 60  # 10:00 ET


def day_features(enriched: dict) -> pd.DataFrame:
    """Per (symbol, day) scanner features from enriched 1m frames."""
    rows = []
    for (sym, iv), E in enriched.items():
        if iv != "1m":
            continue
        day, minute, close = E["day"], E["minute"], E["close"]
        day_open, day_rv = E["day_open"], E["day_relvol"]
        idx = E["index"]
        for d in np.unique(day):
            in_day = day == d
            sel = in_day & (minute >= SELECT_MINUTE)
            if not sel.any():
                continue
            k = int(np.argmax(sel))          # first bar at/after 10:00
            prev_mask = day == d - 1
            prev_close = close[prev_mask][-1] if prev_mask.any() else np.nan
            gap = day_open[k] / prev_close - 1 if np.isfinite(prev_close) else np.nan
            early_move = close[k] / day_open[k] - 1
            rows.append({"symbol": sym, "day": d, "date": idx[k].date(),
                         "gap": gap, "early_move": early_move,
                         "early_rv": float(day_rv[k])})
    return pd.DataFrame(rows)


def build_allowlist(feats: pd.DataFrame, gap_min: float = 0.02, move_min: float = 0.015,
                    rv_min: float = 1.5, top_k: int = 8) -> set[tuple[str, int]]:
    """Return the set of (symbol, day_code) pairs selected by the scanner."""
    f = feats.copy()
    f["gap"] = f["gap"].fillna(0.0)
    elig = f[(f["gap"] >= gap_min) |
             ((f["early_move"] >= move_min) & (f["early_rv"] >= rv_min))].copy()
    elig["score"] = elig["gap"].clip(lower=0) + elig["early_move"].clip(lower=0) \
        + 0.1 * elig["early_rv"]
    picked = elig.sort_values("score", ascending=False).groupby("day", sort=False).head(top_k)
    return set(zip(picked["symbol"], picked["day"]))


def inject_scan_ok(enriched: dict, allowlist: set[tuple[str, int]]) -> None:
    """Attach a per-bar 'scan_ok' flag: symbol selected today and time >= 10:00."""
    for (sym, iv), E in enriched.items():
        if iv != "1m":
            continue
        day, minute = E["day"], E["minute"]
        ok_days = np.array([(sym, int(d)) in allowlist for d in day])
        E["scan_ok"] = ok_days & (minute >= SELECT_MINUTE)
