"""End-to-end regression on real cached market data (skipped when no cache)."""
import os

import numpy as np
import pytest

from engine.backtest import run_portfolio, simulate_symbol
from engine.data import CACHE_DIR, fetch_intraday
from engine.indicators import enrich
from engine.metrics import compute_metrics
from run_final_report import FINAL_CONFIGS

CACHED = os.path.isdir(CACHE_DIR) and any(
    f.startswith("TSLA_5m") for f in os.listdir(CACHE_DIR)) if os.path.isdir(CACHE_DIR) else False


@pytest.mark.skipif(not CACHED, reason="no cached market data")
def test_end_to_end_on_real_data_deterministic():
    df = fetch_intraday("TSLA", "5m", 59)
    assert len(df) > 1000
    E = enrich(df)
    p = FINAL_CONFIGS[0]
    t1 = simulate_symbol("TSLA", E, p)
    t2 = simulate_symbol("TSLA", E, p)
    assert [x.__dict__ for x in t1] == [x.__dict__ for x in t2]  # deterministic
    for t in t1:
        assert t.entry_time.date() == t.exit_time.date()          # same-day always
        assert t.exit_time >= t.entry_time
        assert np.isfinite(t.ret_pct) and abs(t.ret_pct) < 20
        assert 0 < t.risk_pct <= 3.0 + 1e-9
        m_e = t.entry_time.hour * 60 + t.entry_time.minute
        assert p.entry_start_min <= m_e <= p.entry_end_min


@pytest.mark.skipif(not CACHED, reason="no cached market data")
def test_portfolio_metrics_finite_on_real_data():
    trades = []
    for sym in ("TSLA", "NVDA", "PLTR"):
        try:
            df = fetch_intraday(sym, "5m", 59)
        except Exception:
            continue
        if len(df) < 1000:
            continue
        trades += simulate_symbol(sym, enrich(df), FINAL_CONFIGS[0])
    if not trades:
        pytest.skip("no trades produced")
    curve, tdf = run_portfolio(trades, sizing_mode="risk", risk_per_trade_pct=1.5,
                               pos_leverage_cap=2.5, max_concurrent=6)
    m = compute_metrics(curve, tdf)
    assert m["n_trades"] > 0
    for k in ("total_return_pct", "profit_factor", "max_dd_pct", "sharpe"):
        assert np.isfinite(m[k]), k
    assert m["max_dd_pct"] <= 0
    assert 0 <= m["win_rate"] <= 100
