import numpy as np
import pandas as pd

from engine.indicators import (atr, day_context, ema, enrich, lower_high_runs, macd,
                               pullback_runs, relative_volume, rsi, session_cummax,
                               session_open, session_vwap)
from tests.conftest import make_session


def test_ema_converges_to_constant():
    s = pd.Series([5.0] * 100)
    assert abs(ema(s, 9).iloc[-1] - 5.0) < 1e-9


def test_rsi_bounds_and_direction():
    up = pd.Series(np.linspace(1, 2, 100))
    down = pd.Series(np.linspace(2, 1, 100))
    assert 90 <= rsi(up).iloc[-1] <= 100
    assert 0 <= rsi(down).iloc[-1] <= 10
    flat = pd.Series([3.0] * 50)
    assert (rsi(flat).between(0, 100)).all()


def test_macd_hist_positive_in_uptrend():
    s = pd.Series(np.linspace(100, 120, 120))
    line, sig, hist = macd(s)
    assert (line - sig - hist).abs().max() < 1e-12
    assert hist.iloc[20] > 0


def test_atr_positive_and_scales():
    df = make_session()
    a = atr(df)
    assert (a.iloc[15:] > 0).all()
    df2 = df.copy()
    for c in ("Open", "High", "Low", "Close"):
        df2[c] = df2[c] * 2
    assert atr(df2).iloc[-1] > a.iloc[-1] * 1.5


def test_session_vwap_resets_each_day():
    d1 = make_session("2026-08-10")
    d2 = make_session("2026-08-11")
    d2 = d2 * 1.0
    for c in ("Open", "High", "Low", "Close"):
        d2[c] += 50  # second day trades much higher
    df = pd.concat([d1, d2])
    vw = session_vwap(df)
    first_bar_day2 = vw.loc[d2.index[0]]
    tp = (d2["High"].iloc[0] + d2["Low"].iloc[0] + d2["Close"].iloc[0]) / 3
    assert abs(first_bar_day2 - tp) < 1e-9  # reset: vwap == first bar's typical price


def test_relative_volume_spike():
    df = make_session()
    rv = relative_volume(df)
    assert rv.iloc[60] > 3.0          # 5000 vs ~1000 average
    assert 0.5 < rv.iloc[30] < 1.5


def test_runs_counters():
    high = np.array([5.0, 4.0, 3.0, 6.0, 5.5])
    assert list(lower_high_runs(high)) == [0, 1, 2, 0, 1]
    o = np.array([1, 2, 2, 1, 2.0])
    c = np.array([2, 1, 3, 2, 1.0])   # red at 1 and 4
    h = np.array([3, 4, 5, 4, 4.0])   # lower high at 3, equal at 4
    assert list(pullback_runs(o, c, h)) == [0, 1, 0, 1, 2]


def test_session_cummax_and_open():
    day = np.array([0, 0, 0, 1, 1])
    high = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
    assert list(session_cummax(high, day)) == [1, 3, 3, 5, 5]
    op = np.array([10.0, 11, 12, 20, 21])
    assert list(session_open(op, day)) == [10, 10, 10, 20, 20]


def test_day_context_no_lookahead():
    days = [make_session(f"2026-08-{d:02d}") for d in (3, 4, 5, 6, 7, 10)]
    df = pd.concat(days)
    day_codes = pd.factorize(df.index.normalize())[0]
    adr, day_rv = day_context(df, day_codes, lookback_days=3)
    n = len(make_session())
    # first days have no history -> sentinel 99 (filter-neutral), later days real
    assert adr[0] == 99.0
    assert 0 < adr[-1] < 1.0
    # doubling TODAY'S volume must not change today's ADR (previous-days only)
    df2 = df.copy()
    df2.iloc[-n:, df2.columns.get_loc("Volume")] *= 10
    adr2, day_rv2 = day_context(df2, day_codes, lookback_days=3)
    assert np.allclose(adr[-n:], adr2[-n:])
    assert day_rv2[-1] > day_rv[-1] * 5  # but day relvol reacts


def test_enrich_keys_and_alignment(enriched_rally):
    E = enriched_rally
    for k in ("open", "high", "low", "close", "volume", "ema_fast", "ema_slow",
              "vwap", "rsi", "macd_hist", "atr", "relvol", "lh_runs", "pb_runs",
              "hod", "day_open", "adr_pct", "day_relvol", "day", "minute"):
        assert k in E, k
        assert len(E[k]) == len(E["index"]), k
