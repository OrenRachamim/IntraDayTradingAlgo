import numpy as np

from vcp.indicators import pct_return, rolling_max, sma, swing_points


def test_sma_basic():
    x = np.array([1, 2, 3, 4, 5], dtype=float)
    out = sma(x, 3)
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert out[2] == 2.0 and out[4] == 4.0


def test_sma_is_causal():
    x = np.arange(100, dtype=float)
    out = sma(x, 10)
    x2 = x.copy()
    x2[50:] = 999.0  # changing the future must not change the past
    out2 = sma(x2, 10)
    assert np.allclose(out[:50], out2[:50], equal_nan=True)


def test_pct_return():
    x = np.array([100, 110, 121], dtype=float)
    out = pct_return(x, 1)
    assert np.isnan(out[0])
    assert np.allclose(out[1:], [0.10, 0.10])


def test_rolling_max():
    x = np.array([1, 5, 3, 2, 8], dtype=float)
    out = rolling_max(x, 3)
    assert list(out) == [1, 5, 5, 5, 8]


def test_swing_points_detects_peak_and_trough():
    high = np.array([10, 11, 12, 15, 12, 11, 10, 9, 8, 9, 10, 11, 12], dtype=float)
    low = high - 1
    sh, sl = swing_points(high, low, w=2)
    assert sh[3]           # 15 is the local max
    assert sl[8]           # 7 (low of bar 8) is the local min
    assert not sh[5] and not sl[5]
