"""Minervini Trend Template (Stage-2 screen) + cross-sectional RS ranking."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config
from .data import SymbolData
from .indicators import rolling_max, rolling_min, rs_raw_score, sma


def rs_percentiles(symbols: list[str], data: dict[str, SymbolData]) -> dict[str, np.ndarray]:
    """Cross-sectional percentile (0-100) of the IBD-style RS score, per day.

    Returns a per-symbol float32 array aligned to the calendar (NaN where the
    symbol has no score). Rank base = all symbols with a valid score that day.
    """
    n_days = len(next(iter(data.values())).close)
    scores = np.full((n_days, len(symbols)), np.nan, dtype=np.float32)
    for j, sym in enumerate(symbols):
        scores[:, j] = rs_raw_score(data[sym].close).astype(np.float32)

    df = pd.DataFrame(scores)
    pct = np.array((df.rank(axis=1, pct=True, method="average") * 100.0),
                   dtype=np.float32, copy=True)
    # require a minimum cross-section for a meaningful rank
    counts = df.notna().sum(axis=1).to_numpy()
    pct[counts < 20, :] = np.nan
    return {sym: pct[:, j] for j, sym in enumerate(symbols)}


def trend_template_mask(sd: SymbolData, rs_pct: np.ndarray, cfg: Config) -> np.ndarray:
    """Boolean array: does the symbol pass all trend-template criteria at close of day i."""
    c = sd.close.astype(np.float64)
    tt = cfg.tt
    sma50 = sma(c, 50)
    sma150 = sma(c, 150)
    sma200 = sma(c, 200)
    hi52 = rolling_max(sd.high.astype(np.float64), 252)
    lo52 = rolling_min(sd.low.astype(np.float64), 252)
    sma200_prev = np.full_like(sma200, np.nan)
    d = tt.sma200_slope_days
    sma200_prev[d:] = sma200[:-d]

    with np.errstate(invalid="ignore"):
        ok = (
            (c > sma150) & (c > sma200)                     # 1
            & (sma150 > sma200)                             # 2
            & (sma200 > sma200_prev)                        # 3
            & (sma50 > sma150) & (sma50 > sma200)           # 4
            & (c > sma50)                                   # 5
            & (c >= (1.0 + tt.min_pct_above_52w_low) * lo52)   # 6
            & (c >= (1.0 - tt.max_pct_below_52w_high) * hi52)  # 7
            & (rs_pct >= tt.rs_percentile_min)              # 8
        )
    return np.where(np.isnan(c), False, ok)


def liquidity_mask(sd: SymbolData, cfg: Config) -> np.ndarray:
    """Price and 20-day average dollar-volume floors (uses unadjusted dollars)."""
    dv20 = sma(sd.dollar_volume.astype(np.float64), 20)
    # price floor applies to the actual traded (unadjusted) price level; the raw
    # close is dollar_volume / volume where volume > 0
    vol = sd.volume.astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        raw_close = np.where(vol > 0, sd.dollar_volume / vol, np.nan)
        ok = (raw_close >= cfg.universe.min_price) & (dv20 >= cfg.universe.min_dollar_volume)
    return np.where(np.isnan(raw_close) | np.isnan(dv20), False, ok)
