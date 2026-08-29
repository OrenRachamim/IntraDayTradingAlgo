"""Weekly-timeframe VCP detection mapped back to daily execution.

Weekly bases (months long) are detected on resampled W-FRI bars with their own
parameter scale; each resulting Setup is translated to daily indices so the
daily backtester can monitor and trade it exactly like a daily setup.
Causality: a weekly bar completes at the close of its last trading day, so a
setup confirmed on weekly bar i becomes known at that day's close.
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from .config import Config
from .data import SymbolData
from .vcp_detector import Setup, detect_setups


def weekly_view(sd: SymbolData, calendar: pd.DatetimeIndex
                ) -> tuple[SymbolData, np.ndarray] | None:
    """Resample a symbol's daily arrays to weekly bars.

    Returns (weekly SymbolData, eow) where eow[i] is the calendar index of the
    last trading day of weekly bar i."""
    df = pd.DataFrame({"open": sd.open, "high": sd.high, "low": sd.low,
                       "close": sd.close, "volume": sd.volume}, index=calendar)
    g = df.groupby(pd.Grouper(freq="W-FRI"))
    wk = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
               close=("close", "last"), volume=("volume", "sum"))
    pos = pd.Series(np.arange(len(calendar), dtype=np.int64), index=calendar)
    eow = pos.groupby(pd.Grouper(freq="W-FRI")).max().reindex(wk.index)
    keep = eow.notna()
    wk, eow = wk[keep], eow[keep].astype(np.int64)
    if len(wk) < 30:
        return None
    valid = wk["close"].notna().to_numpy()
    if valid.sum() == 0:
        return None
    nz = np.flatnonzero(valid)
    wsd = SymbolData(
        symbol=sd.symbol,
        open=wk["open"].to_numpy(dtype=np.float32),
        high=wk["high"].to_numpy(dtype=np.float32),
        low=wk["low"].to_numpy(dtype=np.float32),
        close=wk["close"].to_numpy(dtype=np.float32),
        volume=wk["volume"].to_numpy(dtype=np.float32),
        dollar_volume=(wk["close"] * wk["volume"]).to_numpy(dtype=np.float32),
        first_idx=int(nz[0]), last_idx=int(nz[-1]),
    )
    return wsd, eow.to_numpy()


def weekly_config(cfg: Config) -> Config:
    """A config whose vcp block carries the weekly-scale parameters (units = weeks)."""
    wcfg = copy.deepcopy(cfg)
    w = cfg.weekly
    v = wcfg.vcp
    v.swing_window = w.swing_window
    v.min_contractions = w.min_contractions
    v.max_contractions = w.max_contractions
    v.contraction_ratio_max = w.contraction_ratio_max
    v.noise_tolerance = w.noise_tolerance
    v.base_max_depth = w.base_max_depth
    v.final_depth_max = w.final_depth_max
    v.base_min_days = w.base_min_weeks
    v.base_max_days = w.base_max_weeks
    v.base_first_depth_min = w.base_first_depth_min
    v.pivot_max_below_base_high = w.pivot_max_below_base_high
    v.pivot_min_below_base_high = 0.0
    v.vdu_ratio_max = w.vdu_ratio_max
    return wcfg


def detect_weekly_setups(sd: SymbolData, calendar: pd.DatetimeIndex,
                         cfg: Config) -> list[Setup]:
    view = weekly_view(sd, calendar)
    if view is None:
        return []
    wsd, eow = view
    out = []
    for s in detect_setups(wsd, weekly_config(cfg)):
        if s.confirm_idx >= len(eow):
            continue
        out.append(Setup(
            symbol=s.symbol,
            confirm_idx=int(eow[s.confirm_idx]),
            pivot=s.pivot, support_low=s.support_low, base_high=s.base_high,
            base_start_idx=int(eow[s.base_start_idx]),
            n_contractions=s.n_contractions, depths=s.depths,
            vdu_ratio=s.vdu_ratio,
        ))
    return out
