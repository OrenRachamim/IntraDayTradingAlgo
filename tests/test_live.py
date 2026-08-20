"""Live-layer tests that need no IB Gateway: config, state DB, risk guards,
params mapping, scanner math, broker bar conversion."""
import io
import logging
import os
import sqlite3
import time
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

import live.notify as notify
import live.state as state
from live.config import LiveConfig, hhmm_to_min, load_config
from live.engine_live import LiveEngine, build_params
from live.scanner_live import _early_relvol
from tests.conftest import make_session


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Keep tests out of the real state DB and out of the operational log.

    Without the logger swap, exercising the risk guards writes lines like
    "KILL SWITCH: day PnL -3.50%" into state/logs/live.log -- indistinguishable
    from a real event when reading the log during a live session.
    """
    monkeypatch.setattr(state, "DB_PATH", str(tmp_path / "test_live.db"))
    quiet = logging.getLogger("live.tests")
    quiet.handlers = [logging.NullHandler()]
    quiet.propagate = False
    monkeypatch.setattr(notify, "_log", quiet)
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


# ---------- scanner fast path ----------

def test_snapshot_field_parsing_rejects_junk():
    from live.scanner_live import _f
    import math
    assert _f(12.5) == 12.5
    for junk in (None, "", float("nan"), -1.0, 0.0, "abc"):
        assert math.isnan(_f(junk)), f"{junk!r} should be unusable"


def test_score_matches_documented_formula():
    from live.scanner_live import _score
    assert _score(0.10, 0.05, 2.0) == pytest.approx(0.10 + 0.05 + 0.2)
    # negatives are floored at zero, relvol still counts
    assert _score(-0.10, -0.05, 3.0) == pytest.approx(0.3)


def test_thin_history_drops_symbol_instead_of_defaulting():
    """A short frame must yield None, never a neutral relvol.

    Returning 1.0 would let a name whose history failed to arrive pass the
    relative-volume gate on an invented number.
    """
    from live.scanner_live import _relvol_from
    thin = make_session("2026-08-10", after="chop").head(50)
    assert _relvol_from(thin, datetime(2026, 8, 10), 600) is None


def test_relvol_returned_with_stale_flag_when_history_is_long_enough():
    from live.scanner_live import _relvol_from
    frames = [make_session(f"2026-08-{d:02d}", after="chop") for d in (3, 4, 5, 6, 7, 10)]
    df = pd.concat(frames)
    got = _relvol_from(df, datetime(2026, 8, 10), 24 * 60)
    assert got is not None
    rv, stale = got
    assert rv > 0
    assert stale is False          # last session in the frame IS 2026-08-10


def test_est_relvol_scales_by_elapsed_session():
    """The snapshot proxy must rank an in-play name above a quiet one."""
    from live.scanner_live import _est_relvol
    at_10 = 10 * 60
    quiet = _est_relvol(volume=5e6, avvolume=120e6, now_minute=at_10)
    in_play = _est_relvol(volume=29e6, avvolume=1.1e6, now_minute=at_10)
    assert in_play > 50 * quiet
    # later in the session the same volume is less remarkable
    assert _est_relvol(5e6, 20e6, at_10) > _est_relvol(5e6, 20e6, 15 * 60)


def test_est_relvol_is_zero_without_average_volume():
    """A missing avVolume must not fabricate a rank."""
    from live.scanner_live import _est_relvol
    assert _est_relvol(1e6, float("nan"), 600) == 0.0
    assert _est_relvol(float("nan"), 1e6, 600) == 0.0


# ---------- dashboard must never state something it cannot know ----------

def _render_to_text(panel) -> str:
    from rich.console import Console
    con = Console(width=100, file=io.StringIO(), force_terminal=False)
    con.print(panel)
    return con.file.getvalue()


class _StubFeed:
    """Minimal stand-in for IBFeed with a controllable portfolio result."""
    def __init__(self, portfolio):
        self._p = portfolio
        from rich.text import Text
        self.status = Text("stub")

    def portfolio(self):
        return self._p


def test_positions_panel_says_unknown_when_broker_unreachable():
    """A dead connection must never render as 'flat'.

    Showing 'no open positions' while unable to reach the broker is the single
    most dangerous thing this dashboard could claim.
    """
    from live.monitor import panel_positions
    out = _render_to_text(panel_positions(_StubFeed(None)))
    assert "UNKNOWN" in out
    assert "flat" not in out.lower()


def test_positions_panel_says_flat_only_when_confirmed_empty():
    from live.monitor import panel_positions
    out = _render_to_text(panel_positions(_StubFeed([])))
    assert "flat" in out.lower()
    assert "UNKNOWN" not in out


def test_positions_panel_without_feed_asks_for_ib():
    from live.monitor import panel_positions
    out = _render_to_text(panel_positions(None))
    assert "--ib" in out
    assert "flat" not in out.lower()


def test_pump_survives_a_dropped_socket():
    """A Gateway restart must not take the dashboard down with it.

    ib.sleep() raises ConnectionError when the socket dies; if that escapes,
    the operator loses the whole screen at the worst possible moment.
    """
    from live.monitor import IBFeed

    class _DeadIB:
        def isConnected(self):
            return True

        def sleep(self, _s):
            raise ConnectionError("Socket disconnect")

        def disconnect(self):
            raise ConnectionError("already gone")

    feed = IBFeed(LiveConfig())
    feed.ib = _DeadIB()
    feed._tickers["AMLX"] = object()
    feed._backfilled = True

    feed.pump(0)                       # must not raise

    assert feed.ib is None, "dead connection must be forgotten"
    assert not feed._tickers, "stale tickers must not survive a reconnect"
    assert feed._backfilled is False
    assert "connection lost" in str(feed.status)


def test_feed_reports_unknown_positions_after_a_drop():
    """Once dropped, the panel must not claim the account is flat."""
    from live.monitor import IBFeed, panel_positions

    feed = IBFeed(LiveConfig())
    feed.drop("connection lost (ConnectionError)")
    feed._next_try = time.time() + 999     # block the reconnect attempt
    out = _render_to_text(panel_positions(feed))
    assert "UNKNOWN" in out
    assert "flat" not in out.lower()


def test_render_survives_a_disconnected_feed():
    """The first frame is drawn before the error-tolerant loop starts.

    portfolio() returns None when the broker is unreachable, and render()
    sizes the positions pane from it -- len(None) crashed the dashboard on
    startup whenever the Gateway was down.
    """
    from live.monitor import IBFeed, render

    feed = IBFeed(LiveConfig())
    feed._next_try = time.time() + 999       # never connects
    assert feed.portfolio() is None
    render(load_config(), 8, feed, 140)      # must not raise


# ---------- the engine must outlive its broker connection ----------

def test_broker_sleep_survives_a_dropped_socket():
    """ib.sleep() raising must not end the trading session.

    The Gateway restarts daily by design. An unguarded sleep turned that into
    a session kill -- which also skips the 15:55 flatten and leaves positions
    open overnight.
    """
    from live.broker import Broker

    class _DeadIB:
        disconnected = False

        def sleep(self, _s):
            raise ConnectionError("Socket disconnect")

        def disconnect(self):
            _DeadIB.disconnected = True

    b = Broker.__new__(Broker)
    b.cfg = LiveConfig()
    b.ib = _DeadIB()
    b.log = notify.get_logger()

    assert b.sleep(0) is False          # reports the drop, does not raise
    assert _DeadIB.disconnected, "a dead socket must be closed before reconnecting"


def test_broker_sleep_reports_success_when_connected():
    from live.broker import Broker

    class _LiveIB:
        def sleep(self, _s):
            return True

    b = Broker.__new__(Broker)
    b.cfg = LiveConfig()
    b.ib = _LiveIB()
    b.log = notify.get_logger()
    assert b.sleep(0) is True


# ---------- telegram fill alerts ----------

def _fill(order_id, side, shares, avg=32.0, pnl=None):
    from datetime import timezone
    f = {"order_id": order_id, "side": side, "shares": float(shares), "avg": avg,
         "symbol": "AMLX", "time": datetime(2026, 8, 18, 16, 45, tzinfo=timezone.utc)}
    if pnl is not None:
        f["pnl"] = pnl
    return f


def _alerter(monkeypatch, sent):
    from live.monitor import FillAlerter
    cfg = LiveConfig(telegram_bot_token="t", telegram_chat_id="c")
    a = FillAlerter(cfg)
    monkeypatch.setattr(a, "_send", lambda f: sent.append(f))
    return a


def test_alerter_stays_silent_on_the_first_poll(monkeypatch):
    """Startup backfills the whole day; announcing it would bury the live one."""
    sent = []
    a = _alerter(monkeypatch, sent)
    a.poll([_fill(1, "BOT", 15868), _fill(2, "SLD", 15868)])
    assert sent == []


def test_alerter_waits_for_partial_fills_to_settle(monkeypatch):
    """Aggregate size grows between refreshes; one order must alert once."""
    sent = []
    a = _alerter(monkeypatch, sent)
    a.poll([])                                     # prime
    a.poll([_fill(7, "BOT", 5000)])                # still filling
    assert sent == [], "must not announce a half-filled order"
    a.poll([_fill(7, "BOT", 12000)])               # still growing
    assert sent == []
    a.poll([_fill(7, "BOT", 12000)])               # settled
    assert len(sent) == 1 and sent[0]["shares"] == 12000
    a.poll([_fill(7, "BOT", 12000)])               # never twice
    assert len(sent) == 1


def test_alerter_reports_both_sides(monkeypatch):
    sent = []
    a = _alerter(monkeypatch, sent)
    a.poll([])
    for _ in range(2):
        a.poll([_fill(1, "BOT", 100), _fill(2, "SLD", 100, pnl=250.0)])
    assert {f["side"] for f in sent} == {"BOT", "SLD"}


def test_alerter_is_disarmed_without_telegram_config():
    from live.monitor import FillAlerter
    assert FillAlerter(LiveConfig()).armed is False
    assert FillAlerter(LiveConfig(telegram_bot_token="t")).armed is False
    assert FillAlerter(LiveConfig(telegram_bot_token="t", telegram_chat_id="c")).armed


def test_send_telegram_no_ops_when_unconfigured(monkeypatch):
    """An unconfigured push must not attempt a network call."""
    import live.notify as n
    called = []
    monkeypatch.setattr(n.requests, "post", lambda *a, **k: called.append(a))
    assert n.send_telegram(LiveConfig(), "hi") is False
    assert called == []


def test_et_stamp_shows_day_and_month_in_eastern_time():
    """Order rows are stored in UTC; the pane is ET throughout.

    16:37 UTC on 18 Aug is 12:37 ET the same day - a straight slice of the
    string would have shown 16:37, and no date at all.
    """
    from datetime import timezone
    from live.monitor import _et_stamp
    utc = datetime(2026, 8, 18, 16, 37, tzinfo=timezone.utc)
    assert _et_stamp(utc) == "18/08 12:37"
    assert _et_stamp("2026-08-18T16:37:00+00:00") == "18/08 12:37"
    # a naive stamp is treated as UTC, matching state.log_order's utcnow()
    assert _et_stamp("2026-08-18T16:37:00") == "18/08 12:37"
    # crossing midnight ET must roll the date back, not just the clock
    assert _et_stamp(datetime(2026, 8, 19, 2, 30, tzinfo=timezone.utc)) == "18/08 22:30"


def test_et_stamp_degrades_instead_of_raising():
    from live.monitor import _et_stamp
    assert _et_stamp("not a date")          # returns something, does not raise
    assert _et_stamp(None)


def test_send_telegram_reports_a_rejected_message_as_failed(monkeypatch):
    """A bad token answers 401 without raising.

    Counting that as sent would leave the dashboard showing alerts in green
    while nothing ever reaches the phone.
    """
    import live.notify as n

    class _Resp:
        ok = False
        status_code = 401
        reason = "Unauthorized"
        content = b"{}"

        def json(self):
            return {"ok": False, "error_code": 401, "description": "Unauthorized"}

    monkeypatch.setattr(n.requests, "post", lambda *a, **k: _Resp())
    cfg = LiveConfig(telegram_bot_token="bad", telegram_chat_id="1")
    assert n.send_telegram(cfg, "hi") is False


def test_send_telegram_reports_success_only_on_ok_true(monkeypatch):
    import live.notify as n

    class _Resp:
        ok = True
        status_code = 200
        reason = "OK"
        content = b"{}"

        def __init__(self, ok_flag):
            self._ok = ok_flag

        def json(self):
            return {"ok": self._ok, "result": {"message_id": 1}}

    monkeypatch.setattr(n.requests, "post", lambda *a, **k: _Resp(True))
    cfg = LiveConfig(telegram_bot_token="good", telegram_chat_id="1")
    assert n.send_telegram(cfg, "hi") is True

    # HTTP 200 with ok=false is still a rejection
    monkeypatch.setattr(n.requests, "post", lambda *a, **k: _Resp(False))
    assert n.send_telegram(cfg, "hi") is False
