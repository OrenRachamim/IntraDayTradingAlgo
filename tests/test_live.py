"""Live-layer tests that need no IB Gateway: config, state DB, risk guards,
params mapping, scanner math, broker bar conversion."""
import os
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

import live.state as state
from live.config import LiveConfig, hhmm_to_min, load_config
from live.engine_live import LiveEngine, build_params
from live.scanner_live import _early_relvol
from tests.conftest import make_session


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DB_PATH", str(tmp_path / "test_live.db"))
    yield


# ---------- config ----------

def test_hhmm():
    assert hhmm_to_min("09:35") == 575
    assert hhmm_to_min("15:55") == 955


def test_config_defaults_sane():
    cfg = LiveConfig()
    assert cfg.port == 4002                      # paper by default
    assert cfg.daily_loss_limit_pct > 0
    assert cfg.max_concurrent >= 1
    assert cfg.risk_per_trade_pct <= 2.0


# ---------- state DB ----------

def test_flags_roundtrip():
    assert state.trading_enabled() is True       # default when unset
    state.set_flag("trading_enabled", "0", "test")
    assert state.trading_enabled() is False
    state.set_flag("trading_enabled", "1", "back")
    assert state.trading_enabled() is True


def test_trade_log_and_stats():
    for ret, pnl in [(1.0, 100), (2.0, 200), (-1.0, -100)]:
        state.log_trade(symbol="X", qty=10, entry_time="a", exit_time="b",
                        entry_px=100, exit_px=100 * (1 + ret / 100), stop_px=99,
                        target_px=103, pnl_usd=pnl, ret_pct=ret, reason="t",
                        mode="paper")
    s = state.live_trade_stats()
    assert s["n"] == 3
    assert abs(s["pf"] - 3.0) < 1e-9
    assert abs(s["win_rate"] - 66.666) < 0.01
    assert s["total_pnl_usd"] == 200


def test_daily_and_scanner_log():
    state.upsert_daily("2026-08-11", start_equity=100000.0)
    state.upsert_daily("2026-08-11", end_equity=101000.0, n_trades=3)
    state.log_scanner("2026-08-11", [{"symbol": "TSLA", "score": 0.1, "gap": 0.05,
                                      "early_move": 0.02, "early_rv": 2.5}])
    state.log_scanner("2026-08-11", [{"symbol": "NVDA", "score": 0.2, "gap": 0.06,
                                      "early_move": 0.03, "early_rv": 3.0}])  # replaces
    with sqlite3.connect(state.DB_PATH) as con:
        d = con.execute("SELECT start_equity, end_equity, n_trades FROM daily").fetchone()
        assert d == (100000.0, 101000.0, 3)
        rows = con.execute("SELECT symbol FROM scanner_log WHERE date='2026-08-11'").fetchall()
        assert rows == [("NVDA",)]                # same-day rerun overwrites


# ---------- params mapping ----------

def test_build_params_maps_config():
    cfg = LiveConfig(relvol_min=2.2, target_rr=2.5, max_pullback_bars=3,
                     require_macd=False, entry_end="14:00", eod_flat="15:50")
    p = build_params(cfg)
    assert p.timeframe == "1m" and p.momentum_mode == "surge"
    assert p.relvol_min == 2.2 and p.target_rr == 2.5
    assert p.macd_filter is False
    assert p.entry_end_min == hhmm_to_min("14:00")
    assert p.eod_exit_min == hhmm_to_min("15:50")
    assert p.in_play_filter is False              # live scanner replaces it


# ---------- risk guards (mocked broker) ----------

def _engine(monkeypatch, open_syms=0, entry_end="23:59"):
    cfg = LiveConfig(entry_end=entry_end, max_concurrent=3, max_trades_per_day=5)
    eng = LiveEngine(cfg)
    monkeypatch.setattr(eng.broker, "open_position_symbols",
                        lambda: {f"S{i}" for i in range(open_syms)})
    return eng


def test_can_enter_happy_path(monkeypatch):
    assert _engine(monkeypatch).can_enter() is True


def test_can_enter_blocked_by_flag(monkeypatch):
    eng = _engine(monkeypatch)
    state.set_flag("trading_enabled", "0", "maintenance")
    assert eng.can_enter() is False


def test_can_enter_blocked_by_kill(monkeypatch):
    eng = _engine(monkeypatch)
    eng.killed = True
    assert eng.can_enter() is False


def test_can_enter_blocked_by_concurrency(monkeypatch):
    assert _engine(monkeypatch, open_syms=3).can_enter() is False


def test_can_enter_blocked_by_trade_count(monkeypatch):
    eng = _engine(monkeypatch)
    eng.trades_today = 5
    assert eng.can_enter() is False


def test_can_enter_blocked_after_entry_window(monkeypatch):
    assert _engine(monkeypatch, entry_end="00:01").can_enter() is False


def test_kill_switch_triggers_flatten(monkeypatch):
    eng = _engine(monkeypatch)
    eng.start_equity = 100_000.0
    monkeypatch.setattr(eng.broker, "equity", lambda: 96_500.0)   # -3.5%
    calls = []
    monkeypatch.setattr(eng.broker, "flatten_all", lambda reason="": calls.append(reason))
    eng.check_kill_switch()
    assert eng.killed is True and calls == ["kill_switch"]
    # small drawdown does NOT trigger
    eng2 = _engine(monkeypatch)
    eng2.start_equity = 100_000.0
    monkeypatch.setattr(eng2.broker, "equity", lambda: 99_000.0)  # -1%
    eng2.check_kill_switch()
    assert eng2.killed is False


# ---------- live scanner math ----------

def test_early_relvol_detects_volume_surge():
    frames = [make_session(f"2026-08-{d:02d}", after="chop") for d in (3, 4, 5, 6, 7)]
    today = make_session("2026-08-10", after="chop")
    today["Volume"] = today["Volume"] * 3
    df = pd.concat(frames + [today]).rename(columns=str.title)
    rv = _early_relvol(df, now_minute=12 * 60)
    assert 2.5 < rv < 3.5
    df_flat = pd.concat(frames + [make_session("2026-08-10", after="chop")])
    assert 0.8 < _early_relvol(df_flat, now_minute=12 * 60) < 1.2


# ---------- broker bar conversion ----------

def test_bars_df_conversion():
    try:
        from ib_async import BarData
    except ImportError:
        pytest.skip("ib_async not installed")
    from live.broker import Broker
    bars = []
    t0 = pd.Timestamp("2026-08-10 14:30", tz="UTC")  # 10:30 ET
    for k in range(3):
        b = BarData(date=(t0 + pd.Timedelta(minutes=k)).to_pydatetime(),
                    open=100 + k, high=101 + k, low=99 + k, close=100.5 + k,
                    volume=1000, average=100.0, barCount=10)
        bars.append(b)
    df = Broker.bars_df(bars)
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert str(df.index.tz) in ("US/Eastern", "America/New_York")
    assert df.index[0].hour == 10 and df.index[0].minute == 30
    assert df["Close"].iloc[-1] == 102.5
