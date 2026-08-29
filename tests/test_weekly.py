import numpy as np
import pandas as pd

from tests.helpers import make_symbol, ramp
from vcp.config import Config
from vcp.weekly import detect_weekly_setups, weekly_view


def _calendar(n):
    return pd.DatetimeIndex(pd.bdate_range("2018-01-01", periods=n))


def test_weekly_view_aggregates_bars():
    n = 250
    sd = make_symbol(list(np.linspace(50, 100, n)))
    cal = _calendar(n)
    wsd, eow = weekly_view(sd, cal)
    assert len(wsd.close) == len(eow)
    assert 45 <= len(wsd.close) <= 55            # ~50 weeks
    # weekly high >= weekly close, and eow maps into the daily calendar
    assert np.nanmax(wsd.high) >= np.nanmax(wsd.close) - 1e-6
    assert eow.max() < n and (np.diff(eow) > 0).all()


def test_detects_weekly_scale_vcp():
    # ~2y: uptrend then 3 weekly-scale contractions (25% -> 12% -> 5%),
    # each leg several weeks long, volume drying up
    closes, vols = [], []
    closes += ramp(30, 100, 250);            vols += [2e6] * 250
    closes += ramp(100, 75, 40)[1:];         vols += [1.8e6] * 39
    closes += ramp(75, 98, 40)[1:];          vols += [1.5e6] * 39
    closes += ramp(98, 86, 30)[1:];          vols += [1.1e6] * 29
    closes += ramp(86, 96, 30)[1:];          vols += [0.9e6] * 29
    closes += ramp(96, 91, 20)[1:];          vols += [0.5e6] * 19
    closes += ramp(91, 94, 15)[1:];          vols += [0.5e6] * 14
    sd = make_symbol(closes, volumes=vols)
    cal = _calendar(len(closes))
    cfg = Config()
    cfg.weekly.enabled = True
    setups = detect_weekly_setups(sd, cal, cfg)
    assert setups, "weekly-scale VCP should be detected"
    s = setups[-1]
    assert s.confirm_idx < len(closes)           # mapped to a daily index
    assert abs(s.pivot - 96 * 1.01) / s.pivot < 0.03
    assert s.depths[-1] <= cfg.weekly.contraction_ratio_max * s.depths[0] + 1e-9
