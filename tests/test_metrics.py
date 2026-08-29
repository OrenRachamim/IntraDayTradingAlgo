import math

import numpy as np
import pandas as pd

from vcp.metrics import cagr, max_drawdown, sharpe, summarize


def _series(values, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def test_cagr_doubling_in_one_year():
    idx = pd.DatetimeIndex([pd.Timestamp("2020-01-01"), pd.Timestamp("2021-01-01")])
    eq = pd.Series([100.0, 200.0], index=idx)
    assert abs(cagr(eq) - 1.0) < 0.01


def test_max_drawdown():
    eq = _series([100, 120, 60, 90])
    assert math.isclose(max_drawdown(eq), -0.5)


def test_sharpe_positive_for_steady_gains():
    eq = _series(list(np.linspace(100, 150, 100)))
    assert sharpe(eq) > 3


def test_summarize_profit_multiple():
    eq = _series(list(np.linspace(100_000, 300_000, 300)))     # +200%
    spy = _series(list(np.linspace(100, 200, 300)))            # +100%
    s = summarize(eq, [], eq * 0 + 0.5, spy)
    assert math.isclose(s["profit_multiple_vs_spy"], 2.0, rel_tol=1e-6)
