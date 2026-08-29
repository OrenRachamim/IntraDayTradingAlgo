"""Vectorized indicator helpers (NaN-aware, causal: value at i uses bars <= i)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(x: np.ndarray, window: int) -> np.ndarray:
    return pd.Series(x, dtype="float64").rolling(window, min_periods=window).mean().to_numpy()


def rolling_max(x: np.ndarray, window: int) -> np.ndarray:
    return pd.Series(x, dtype="float64").rolling(window, min_periods=1).max().to_numpy()


def rolling_min(x: np.ndarray, window: int) -> np.ndarray:
    return pd.Series(x, dtype="float64").rolling(window, min_periods=1).min().to_numpy()


def pct_return(close: np.ndarray, periods: int) -> np.ndarray:
    """close[i] / close[i - periods] - 1 (NaN where unavailable)."""
    s = pd.Series(close, dtype="float64")
    return (s / s.shift(periods) - 1.0).to_numpy()


def rs_raw_score(close: np.ndarray) -> np.ndarray:
    """IBD-style weighted momentum: 2*3m + 6m + 9m + 12m returns."""
    return (2.0 * pct_return(close, 63) + pct_return(close, 126)
            + pct_return(close, 189) + pct_return(close, 252))


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int = 20) -> np.ndarray:
    """Average True Range (simple moving average of true range)."""
    h = pd.Series(high, dtype="float64")
    l = pd.Series(low, dtype="float64")
    c_prev = pd.Series(close, dtype="float64").shift(1)
    tr = pd.concat([h - l, (h - c_prev).abs(), (l - c_prev).abs()], axis=1).max(axis=1)
    return tr.rolling(window, min_periods=window).mean().to_numpy()


def swing_points(high: np.ndarray, low: np.ndarray, w: int) -> tuple[np.ndarray, np.ndarray]:
    """Boolean masks of swing highs/lows: bar i is a swing high if high[i] is the
    maximum of high[i-w .. i+w] (strictly greater than all neighbors that differ).
    A swing at i is only *known* w bars later; callers must add that lag."""
    n = len(high)
    hi = pd.Series(high, dtype="float64")
    lo = pd.Series(low, dtype="float64")
    win = 2 * w + 1
    # center=True rolling max/min over the full window
    hmax = hi.rolling(win, min_periods=1, center=True).max().to_numpy()
    lmin = lo.rolling(win, min_periods=1, center=True).min().to_numpy()
    sh = (high >= hmax) & ~np.isnan(high)
    sl = (low <= lmin) & ~np.isnan(low)
    # de-duplicate flat tops: keep only the first bar of a run of equal highs
    for mask, arr in ((sh, high), (sl, low)):
        idxs = np.flatnonzero(mask)
        prev = -10**9
        prev_val = np.nan
        for i in idxs:
            if i - prev <= w and arr[i] == prev_val:
                mask[i] = False
            else:
                prev, prev_val = i, arr[i]
    return sh, sl
