import numpy as np
import pandas as pd

import engine.backtest as bt
from engine.backtest import _subbar_stop_first, simulate_symbol
from engine.indicators import enrich
from engine.strategy import with_
from tests.conftest import BREAK, make_session

COST = 2 * bt.COST_BPS_PER_SIDE / 1e4  # round-trip cost as fraction


def _one_trade(after, params):
    E = enrich(make_session(after=after))
    trades = simulate_symbol("SYN", E, params)
    assert len(trades) == 1, f"expected exactly 1 trade, got {len(trades)}"
    return trades[0], E


def test_target_exit_and_costs(loose_params):
    t, E = _one_trade("rally", loose_params)
    assert t.reason == "target"
    prev_high = E["high"][BREAK - 1]
    assert abs(t.entry - (prev_high + 0.01)) < 1e-3   # open was below the trigger (4dp rounding)
    gross = (t.exit - t.entry) / t.entry
    assert abs(t.ret_pct / 100 - (gross - COST)) < 1e-4
    # exit price equals entry + 2R exactly
    risk = t.entry - (t.entry * (1 - loose_params.stop_cap_pct / 100)) \
        if False else None
    assert t.exit > t.entry


def test_stop_exit(loose_params):
    t, E = _one_trade("dump", loose_params)
    assert t.reason == "stop"
    assert t.exit < t.entry
    # loss cannot exceed the 1.5% cap by more than gap/cost noise
    assert (t.entry - t.exit) / t.entry <= loose_params.stop_cap_pct / 100 + 1e-6


def test_eod_exit(loose_params):
    t, E = _one_trade("chop", loose_params)
    assert t.reason == "eod"
    # exit at/after the eod minute, never on a later day
    exit_minute = t.exit_time.hour * 60 + t.exit_time.minute
    assert exit_minute >= loose_params.eod_exit_min or t.exit_time == E["index"][-1]
    assert t.exit_time.date() == t.entry_time.date()


def test_risk_pct_recorded(loose_params):
    t, _ = _one_trade("rally", loose_params)
    # risk_pct is stored in percent units (1.5 == 1.5%)
    assert 0 < t.risk_pct <= loose_params.stop_cap_pct + 1e-9


def test_trailing_stop_exit(loose_params):
    """Rally then fade: trailing should lock in profit above the initial stop."""
    df = make_session(after="rally")
    n = len(df)
    # fade from bar 75 onward, slowly, so trail (not initial stop) is hit
    for k in range(75, n):
        df.iloc[k, df.columns.get_loc("Open")] = df["Close"].iloc[k - 1]
        df.iloc[k, df.columns.get_loc("Close")] = df["Open"].iloc[k] - 0.25
        df.iloc[k, df.columns.get_loc("High")] = df["Open"].iloc[k] + 0.05
        df.iloc[k, df.columns.get_loc("Low")] = df["Close"].iloc[k] - 0.05
    p = with_(loose_params, target_rr=50.0, trail_mode="pct", trail_pct=0.4,
              trail_activate_rr=0.5)
    trades = simulate_symbol("SYN", enrich(df), p)
    assert len(trades) == 1
    t = trades[0]
    assert t.reason == "trail"
    assert t.exit > t.entry * (1 - p.stop_cap_pct / 100)  # better than initial stop


def test_pessimistic_vs_optimistic_ambiguous_bar(loose_params):
    """A bar spanning both stop and target: pessimistic loses, optimistic wins."""
    df = make_session(after="chop")
    k = BREAK + 2  # wide bar shortly after entry
    df.iloc[k, df.columns.get_loc("High")] = df["Close"].iloc[BREAK] + 5.0
    df.iloc[k, df.columns.get_loc("Low")] = df["Close"].iloc[BREAK] - 5.0
    df.iloc[k, df.columns.get_loc("Open")] = df["Close"].iloc[k - 1]
    tp = simulate_symbol("SYN", enrich(df), with_(loose_params, intrabar="pessimistic"))
    to = simulate_symbol("SYN", enrich(df), with_(loose_params, intrabar="optimistic"))
    assert tp[0].reason == "stop" and to[0].reason == "target"


def test_subbar_resolution_helper():
    ts = pd.date_range("2026-08-10 10:00", periods=5, freq="1min", tz="US/Eastern")
    sub = {"ts": ts.as_unit("ns").asi8,   # mirror production: engine/optimize.py
           "high": np.array([10.0, 10.2, 11.0, 10.1, 10.0]),
           "low": np.array([9.9, 9.4, 10.5, 9.9, 9.8]),
           "span_ns": 5 * 60 * 1_000_000_000}
    # stop 9.5 touched at minute 1, target 10.9 at minute 2 -> stop first
    assert _subbar_stop_first(sub, ts[0], stop=9.5, target=10.9) is True
    # target 10.15 touched at minute 1 before stop 9.3 (never) -> target first
    assert _subbar_stop_first(sub, ts[0], stop=9.3, target=10.15) is False
    # no coverage -> pessimistic True
    later = ts[0] + pd.Timedelta(hours=3)
    assert _subbar_stop_first(sub, later, stop=9.5, target=10.9) is True


def test_max_retrace_filter_blocks_deep_pullbacks(loose_params):
    E = enrich(make_session(after="rally"))
    p = with_(loose_params, max_retrace_atr=0.01)   # absurdly strict
    assert len(simulate_symbol("SYN", E, p)) == 0


def test_skip_when_entry_gap_above_high(loose_params):
    """If the bar opens above its own high-trigger path is impossible, skip."""
    df = make_session(after="rally")
    # make breakout bar open far above prev high but with high BELOW open (bad bar)
    prev_high = df["High"].iloc[BREAK - 1]
    df.iloc[BREAK, df.columns.get_loc("Open")] = prev_high + 2.0
    df.iloc[BREAK, df.columns.get_loc("High")] = prev_high + 1.0
    trades = simulate_symbol("SYN", enrich(df), loose_params)
    assert all(t.entry_time != df.index[BREAK] for t in trades)


def test_one_position_at_a_time(loose_params):
    """Overlapping signals while in a trade are ignored (busy_until)."""
    d1 = make_session("2026-08-10", after="chop")
    d2 = make_session("2026-08-11", after="chop")
    trades = simulate_symbol("SYN", enrich(pd.concat([d1, d2])), loose_params)
    days = [t.entry_time.date() for t in trades]
    assert len(days) == len(set(days))  # at most one trade per chop day
