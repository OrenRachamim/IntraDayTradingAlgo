import math
from types import SimpleNamespace

import numpy as np
import pandas as pd

from tests.helpers import make_symbol
from vcp.backtester import Backtester
from vcp.config import Config
from vcp.vcp_detector import Setup


def _cfg(**risk_overrides) -> Config:
    cfg = Config()
    cfg.costs.slippage_bps = 0.0
    cfg.costs.commission_bps = 0.0
    cfg.entry.market_filter = False
    cfg.exit.trail_ma = 0
    cfg.exit.target_R = 0.0
    cfg.exit.breakeven_at_R = 0.0
    cfg.backtest.initial_capital = 100_000.0
    for k, v in risk_overrides.items():
        setattr(cfg.risk, k, v)
    return cfg


def _run(sd, closes_len, setup, cfg):
    cal = pd.DatetimeIndex(pd.bdate_range("2020-01-01", periods=closes_len))
    market = SimpleNamespace(regime_ok=np.ones(closes_len, dtype=bool),
                             close=np.full(closes_len, 100.0))
    masks = {sd.symbol: np.ones(closes_len, dtype=bool)}
    rs = {sd.symbol: np.full(closes_len, 90.0, dtype=np.float32)}
    engine = Backtester(cfg, cal, {sd.symbol: sd}, {sd.symbol: [setup]},
                        masks, masks, rs, market, start_idx=1)
    return engine.run()


def _flat_then(closes_pre, post):
    return closes_pre + post


def test_entry_at_trigger_and_stop_exit():
    # flat at 95 (below pivot 100); day 12 opens at 100 and its high crosses the
    # trigger intraday (spread 2%), then the stock collapses through the stop
    closes = [95.0] * 12 + [100.0, 99.0, 88.0] + [88.0] * 5
    sd = make_symbol(closes, spread=0.02)
    setup = Setup(symbol="TEST", confirm_idx=10, pivot=100.0, support_low=92.0,
                  base_high=105.0, base_start_idx=2, n_contractions=3,
                  depths=(0.2, 0.1, 0.04), vdu_ratio=0.5)
    cfg = _cfg()
    res = _run(sd, len(closes), setup, cfg)
    assert len(res.trades) == 1
    tr = res.trades[0]
    trigger = 100.0 * (1 + cfg.entry.breakout_buffer)
    assert tr.entry_idx == 12                      # first bar whose high crosses the trigger
    assert abs(tr.entry_price - trigger) < 1e-6    # buy-stop fill at the trigger, not the close
    assert tr.reason in ("stop", "stop_gap")
    # stop respected: exit at or below the initial stop only via gap
    assert tr.exit_price <= tr.init_stop + 1e-6
    # accounting: final equity = initial + pnl
    assert math.isclose(res.equity.iloc[-1], 100_000 + tr.pnl, rel_tol=1e-9)


def test_no_entry_before_setup_confirmed():
    # price crosses the pivot BEFORE the setup exists -> no trade until after confirm
    closes = [101.0] * 5 + [95.0] * 10 + [95.0] * 5
    sd = make_symbol(closes, spread=0.0)
    setup = Setup(symbol="TEST", confirm_idx=10, pivot=100.0, support_low=92.0,
                  base_high=105.0, base_start_idx=2, n_contractions=2,
                  depths=(0.2, 0.05), vdu_ratio=0.5)
    res = _run(sd, len(closes), setup, _cfg())
    assert res.trades == []          # never crossed the trigger after confirmation


def test_gap_down_stop_fills_at_open():
    closes = [95.0] * 12 + [101.0, 103.0, 80.0] + [80.0] * 5
    sd = make_symbol(closes, spread=0.0)
    # day 14 opens at 80, far below any stop -> fill at open, not at stop level
    setup = Setup(symbol="TEST", confirm_idx=10, pivot=100.0, support_low=92.0,
                  base_high=105.0, base_start_idx=2, n_contractions=2,
                  depths=(0.2, 0.05), vdu_ratio=0.5)
    res = _run(sd, len(closes), setup, _cfg())
    tr = res.trades[0]
    assert tr.reason == "stop_gap"
    assert abs(tr.exit_price - 80.0) < 1e-6


def test_profit_target_sells_into_strength():
    closes = [95.0] * 12 + [101.0] + list(np.linspace(101, 140, 10)) + [140.0] * 3
    sd = make_symbol(closes, spread=0.0)
    setup = Setup(symbol="TEST", confirm_idx=10, pivot=100.0, support_low=95.0,
                  base_high=105.0, base_start_idx=2, n_contractions=2,
                  depths=(0.2, 0.05), vdu_ratio=0.5)
    cfg = _cfg()
    cfg.exit.target_R = 2.0
    res = _run(sd, len(closes), setup, cfg)
    tr = res.trades[0]
    assert tr.reason in ("target", "target_gap")
    risk = tr.entry_price - tr.init_stop
    assert tr.exit_price >= tr.entry_price + 2.0 * risk - 1e-6
    assert tr.pnl > 0


def test_position_sizing_respects_risk_budget():
    closes = [95.0] * 12 + [101.0] + [101.0] * 10
    sd = make_symbol(closes, spread=0.0)
    setup = Setup(symbol="TEST", confirm_idx=10, pivot=100.0, support_low=95.0,
                  base_high=105.0, base_start_idx=2, n_contractions=2,
                  depths=(0.2, 0.05), vdu_ratio=0.5)
    cfg = _cfg(risk_per_trade=0.01, max_weight=0.99)
    cfg.risk.stop_use_contraction_low = True
    res = _run(sd, len(closes), setup, cfg)
    tr = res.trades[0]
    risk_taken = (tr.entry_price - tr.init_stop) * tr.shares
    assert risk_taken <= 0.01 * 100_000 + tr.entry_price  # within budget (+1 share rounding)


def test_max_weight_caps_position():
    closes = [95.0] * 12 + [101.0] + [101.0] * 10
    sd = make_symbol(closes, spread=0.0)
    setup = Setup(symbol="TEST", confirm_idx=10, pivot=100.0, support_low=95.0,
                  base_high=105.0, base_start_idx=2, n_contractions=2,
                  depths=(0.2, 0.05), vdu_ratio=0.5)
    cfg = _cfg(risk_per_trade=0.5, max_weight=0.20)   # huge risk budget, small cap
    res = _run(sd, len(closes), setup, cfg)
    tr = res.trades[0]
    assert tr.shares * tr.entry_price <= 0.20 * 100_000 + tr.entry_price


def test_delisting_force_closes():
    closes = [95.0] * 12 + [101.0, 102.0, 103.0]
    sd = make_symbol(closes + [np.nan] * 5, spread=0.0)
    sd.last_idx = len(closes) - 1                      # symbol stops trading
    setup = Setup(symbol="TEST", confirm_idx=10, pivot=100.0, support_low=95.0,
                  base_high=105.0, base_start_idx=2, n_contractions=2,
                  depths=(0.2, 0.05), vdu_ratio=0.5)
    res = _run(sd, len(closes) + 5, setup, _cfg())
    assert len(res.trades) == 1
    assert res.trades[0].reason == "delisted"


def test_chase_guard_skips_runaway_gap():
    # opens 10% above the pivot -> too extended, must not chase
    closes = [95.0] * 12 + [110.0] + [110.0] * 5
    sd = make_symbol(closes, spread=0.0)
    setup = Setup(symbol="TEST", confirm_idx=10, pivot=100.0, support_low=95.0,
                  base_high=105.0, base_start_idx=2, n_contractions=2,
                  depths=(0.2, 0.05), vdu_ratio=0.5)
    res = _run(sd, len(closes), setup, _cfg())
    assert res.trades == []
