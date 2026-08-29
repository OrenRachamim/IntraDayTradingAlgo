"""Synthetic data builders for tests."""
from __future__ import annotations

import numpy as np

from vcp.data import SymbolData


def make_symbol(closes, symbol="TEST", volumes=None, spread=0.01) -> SymbolData:
    """Build a SymbolData from a close path; high/low straddle close by `spread`."""
    c = np.asarray(closes, dtype=np.float32)
    n = len(c)
    v = (np.full(n, 1_000_000.0, dtype=np.float32) if volumes is None
         else np.asarray(volumes, dtype=np.float32))
    high = c * (1 + spread)
    low = c * (1 - spread)
    openp = c.copy()
    return SymbolData(symbol=symbol, open=openp, high=high, low=low, close=c,
                      volume=v, dollar_volume=(c * v).astype(np.float32),
                      first_idx=0, last_idx=n - 1)


def ramp(a: float, b: float, n: int) -> list[float]:
    return list(np.linspace(a, b, n))


def vcp_price_path() -> tuple[list[float], list[float]]:
    """Uptrend then 3 tightening contractions (20% -> ~9% -> ~3%), volume drying up.

    Returns (closes, volumes). The pivot of the final contraction is 96.
    """
    closes: list[float] = []
    vols: list[float] = []
    closes += ramp(50, 100, 60);              vols += [2e6] * 60          # advance
    closes += ramp(100, 80, 10)[1:];          vols += [1.8e6] * 9         # T1 down 20%
    closes += ramp(80, 98, 10)[1:];           vols += [1.5e6] * 9
    closes += ramp(98, 89, 9)[1:];            vols += [1.2e6] * 8         # T2 down ~9.2%
    closes += ramp(89, 96, 9)[1:];            vols += [1.0e6] * 8
    closes += ramp(96, 93, 8)[1:];            vols += [0.5e6] * 7         # T3 down ~3.1%
    closes += ramp(93, 95, 8)[1:];            vols += [0.5e6] * 7         # drift under pivot
    return closes, vols
