import numpy as np
import pandas as pd
from types import SimpleNamespace

from tests.helpers import make_symbol
from vcp.config import Config
from vcp.scanner import plan_position, scan_symbol
from vcp.vcp_detector import Setup


def _market(n, on=True):
    return SimpleNamespace(regime_ok=np.full(n, on, dtype=bool),
                           close=np.full(n, 100.0))


def _setup(**kw):
    base = dict(symbol="TEST", confirm_idx=10, pivot=100.0, support_low=92.0,
                base_high=105.0, base_start_idx=2, n_contractions=3,
                depths=(0.2, 0.1, 0.04), vdu_ratio=0.5)
    base.update(kw)
    return Setup(**base)


def _cfg():
    cfg = Config()
    cfg.tt.rs_percentile_min = 0.0        # neutralize TT's RS criterion for tests
    return cfg


def test_pending_setup_is_reported_with_plan():
    closes = list(np.linspace(50, 95, 380)) + [95.0] * 20   # uptrend, then under pivot
    sd = make_symbol(closes)
    n = len(closes)
    cfg = _cfg()
    row = scan_symbol(sd, [_setup(confirm_idx=n - 10)], np.full(n, 90.0),
                      np.ones(n, bool), _market(n), cfg, n - 1,
                      equity=100_000, max_staleness_days=5)
    assert row is not None
    assert row.trigger > 100.0 and row.stop < row.trigger
    assert row.shares > 0
    assert row.dist_to_trigger_pct > 0


def test_broken_support_not_reported():
    closes = list(np.linspace(50, 95, 380)) + [95.0] * 10 + [85.0] * 10  # breaks 92
    sd = make_symbol(closes)
    n = len(closes)
    row = scan_symbol(sd, [_setup(confirm_idx=n - 15)], np.full(n, 90.0),
                      np.ones(n, bool), _market(n), _cfg(), n - 1,
                      equity=100_000, max_staleness_days=5)
    assert row is None


def test_already_broken_out_not_reported():
    closes = list(np.linspace(50, 95, 380)) + [95.0] * 5 + [103.0] * 5   # crossed pivot
    sd = make_symbol(closes)
    n = len(closes)
    row = scan_symbol(sd, [_setup(confirm_idx=n - 8)], np.full(n, 90.0),
                      np.ones(n, bool), _market(n), _cfg(), n - 1,
                      equity=100_000, max_staleness_days=5)
    assert row is None


def test_stale_symbol_excluded():
    closes = list(np.linspace(50, 95, 380)) + [95.0] * 10
    sd = make_symbol(closes)
    n = len(closes)
    sd.last_idx = n - 30                    # data ends 30 bars before asof
    row = scan_symbol(sd, [_setup(confirm_idx=n - 40)], np.full(n, 90.0),
                      np.ones(n, bool), _market(n), _cfg(), n - 1,
                      equity=100_000, max_staleness_days=5)
    assert row is None


def test_plan_position_respects_risk():
    cfg = Config()
    stop, shares = plan_position(cfg, trigger=100.0, support_low=95.0, equity=100_000)
    assert 90.0 <= stop < 100.0
    risk = shares * (100.0 * 1.0012 - stop)
    assert risk <= cfg.risk.risk_per_trade * 100_000 * 1.05   # within budget + rounding
