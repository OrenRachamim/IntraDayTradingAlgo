"""Shared fixtures: synthetic OHLCV sessions with a textbook micro pullback."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.indicators import enrich
from engine.strategy import Params

N = 90            # bars per synthetic session (5m bars, 09:30 start)
SURGE = 60        # index of the surge bar
PB = [61, 62]     # pullback bars (lower highs)
BREAK = 63        # breakout bar


def make_session(day: str = "2026-08-10", breakout: bool = True,
                 after: str = "rally") -> pd.DataFrame:
    """One session: quiet uptrend, surge at SURGE, 2-bar pullback, breakout.

    after: 'rally' (target hit), 'dump' (stop hit), 'chop' (EOD exit).
    """
    idx = pd.date_range(f"{day} 09:30", periods=N, freq="5min", tz="US/Eastern")
    close = np.linspace(100.0, 101.0, N).copy()
    o = close.copy()
    h = close + 0.10
    l = close - 0.10
    v = np.full(N, 1000.0)

    # surge: +2.0 green bar on 5x volume
    o[SURGE] = close[SURGE - 1]
    close[SURGE] = o[SURGE] + 2.0
    h[SURGE] = close[SURGE] + 0.10
    l[SURGE] = o[SURGE] - 0.05
    v[SURGE] = 5000.0

    # pullback: two lower-high red bars holding well above the base
    for k, drop in zip(PB, (0.30, 0.50)):
        h[k] = close[SURGE] - drop + 0.10
        o[k] = h[k] - 0.02
        close[k] = h[k] - 0.12
        l[k] = h[k] - 0.30
        v[k] = 800.0

    prev_high = h[BREAK - 1]
    if breakout:
        o[BREAK] = prev_high - 0.05
        h[BREAK] = prev_high + 0.60
        close[BREAK] = prev_high + 0.50
        l[BREAK] = o[BREAK] - 0.05
        v[BREAK] = 3000.0
    else:  # no breakout: keeps making lower highs
        h[BREAK] = h[BREAK - 1] - 0.05
        o[BREAK] = h[BREAK] - 0.02
        close[BREAK] = h[BREAK] - 0.10
        l[BREAK] = h[BREAK] - 0.25

    for k in range(BREAK + 1, N):
        if after == "rally":
            o[k] = close[k - 1]
            close[k] = o[k] + 0.30
            h[k] = close[k] + 0.10
            l[k] = o[k] - 0.05
        elif after == "dump":
            o[k] = close[k - 1]
            close[k] = o[k] - 0.60
            h[k] = o[k] + 0.05
            l[k] = close[k] - 0.10
        else:  # chop: tiny drift, never reaches stop or target
            o[k] = close[k - 1]
            close[k] = o[k] + 0.005
            h[k] = o[k] + 0.05
            l[k] = o[k] - 0.05
        v[k] = 1000.0
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": close, "Volume": v},
                        index=idx)


@pytest.fixture
def loose_params() -> Params:
    """Permissive params that fire on the synthetic textbook pattern."""
    return Params(timeframe="5m", momentum_mode="surge", pullback_def="lower_high",
                  momentum_min_gain_atr=1.0, relvol_min=1.2, macd_filter=False,
                  rsi_filter=False, stop_mode="pullback_low", target_rr=2.0,
                  trail_mode="none")


@pytest.fixture
def session_rally() -> pd.DataFrame:
    return make_session(after="rally")


@pytest.fixture
def enriched_rally(session_rally) -> dict:
    return enrich(session_rally)
