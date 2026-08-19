"""Micro Pullback strategy: parameter set and vectorized signal detection."""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace

import numpy as np


@dataclass(frozen=True)
class Params:
    timeframe: str = "5m"
    # trend / signal
    require_above_vwap: bool = True
    momentum_mode: str = "surge"      # surge | hod | either
    momentum_lookback: int = 4        # bars over which the surge is measured
    momentum_min_gain_atr: float = 1.5  # surge strength, in ATRs
    hod_dist_atr: float = 1.0         # 'hod' mode: max distance from high-of-day, in ATRs
    hod_day_gain_atr: float = 2.0     # 'hod' mode: min day gain from open, in ATRs
    relvol_min: float = 1.3           # relative volume on the surge bar
    pullback_def: str = "lower_high"  # lower_high | red_or_lh
    min_pullback_bars: int = 1
    max_pullback_bars: int = 3
    pullback_hold_ema: bool = False   # pullback low must hold above EMA fast
    max_retrace_atr: float = 99.0     # max pullback depth from pre-pullback high, in ATRs
    market_filter: bool = False       # only trade when SPY is above its session VWAP
    in_play_filter: bool = False      # only trade "stocks in play" today
    in_play_gain_adr: float = 0.5     # day gain from open >= this fraction of avg daily range
    in_play_relvol: float = 1.3       # cumulative day volume vs usual at this time
    min_adr_pct: float = 0.0          # min avg daily range (fraction, e.g. 0.02 = 2%)
    scanner_filter: bool = False      # only trade (symbol, day) pairs picked by the scanner
    # filters
    rsi_filter: bool = False
    rsi_min: float = 50.0
    rsi_max: float = 80.0
    macd_filter: bool = False         # require MACD histogram > 0 at setup
    macd_rising: bool = False         # additionally require histogram rising
    # risk management
    stop_mode: str = "pullback_low"   # pullback_low | atr | pct
    stop_atr_mult: float = 1.0
    stop_pct: float = 0.5             # percent
    stop_cap_pct: float = 1.5         # max risk from entry, caps pullback_low stops
    target_mode: str = "rr"           # rr | pct
    target_rr: float = 2.0
    target_pct: float = 1.0
    trail_mode: str = "none"          # none | atr | pct
    trail_atr_mult: float = 1.5
    trail_pct: float = 0.4
    trail_activate_rr: float = 1.0    # trailing arms after this many R of profit
    # session timing (minutes from midnight ET)
    entry_start_min: int = 9 * 60 + 35
    entry_end_min: int = 15 * 60 + 30
    eod_exit_min: int = 15 * 60 + 55
    # execution model for bars containing both stop and target
    intrabar: str = "pessimistic"     # pessimistic | optimistic | subbar
    # portfolio sizing
    sizing_mode: str = "risk"         # risk | notional
    risk_per_trade_pct: float = 1.0   # % of equity risked per trade (risk mode)
    pos_leverage_cap: float = 2.0     # max notional per position, in units of equity
    max_concurrent: int = 4

    def label(self) -> str:
        d = asdict(self)
        return ",".join(f"{k}={v}" for k, v in d.items())


def with_(p: Params, **kw) -> Params:
    return replace(p, **kw)


def entry_gates(E: dict, p: Params, i: int) -> list[tuple[str, bool]]:
    """Per-condition status for a candidate entry bar, in scan_signals' order.

    Decomposes the single boolean scan_signals produces so a dashboard can say
    *what is missing* rather than only "no signal". Every gate mirrors the
    corresponding line in scan_signals; `test_entry_gates_match_scan_signals`
    asserts that all gates passing is equivalent to a signal firing on bar i,
    which is what keeps the two from drifting apart.
    """
    n = len(E["high"])
    if i < 1 or i >= n:
        return []
    close, high, low = E["close"], E["high"], E["low"]
    j = i - 1                       # every condition is judged one bar back
    lb = p.momentum_lookback
    L = E["lh_runs"] if p.pullback_def == "lower_high" else E["pb_runs"]

    valid = i >= (lb + p.max_pullback_bars + 2)
    pb_len = int(L[j]) if valid else 0
    s = max(0, j - pb_len)          # the bar the surge must have happened on

    gain = close[s] - close[s - lb] if s >= lb else float("-inf")
    surge_raw = bool(gain >= p.momentum_min_gain_atr * E["atr"][s]
                     and close[s] > E["open"][s])
    hod_ctx = bool((E["hod"][s] - close[s]) <= p.hod_dist_atr * E["atr"][s]
                   and (close[s] - E["day_open"][s]) >= p.hod_day_gain_atr * E["atr"][s])
    surge = {"surge": surge_raw, "hod": hod_ctx}.get(
        p.momentum_mode, surge_raw or hod_ctx)

    g: list[tuple[str, bool]] = [("warmup", bool(valid))]
    day = E["day"]
    g.append(("session", bool(day[i] == day[j]
                             and day[i] == day[max(0, s - lb)])))
    if p.require_above_vwap:
        g.append(("VWAP", bool(close[j] > E["vwap"][j])))
    g.append(("EMA", bool(E["ema_fast"][j] > E["ema_slow"][j])))
    if p.pullback_hold_ema:
        g.append(("holdEMA", bool(low[j] >= E["ema_fast"][j])))
    if p.macd_filter:
        ok = bool(E["macd_hist"][j] > 0)
        if p.macd_rising:
            ok = ok and bool(E["macd_hist"][j] > E["macd_hist"][j - 1])
        g.append(("MACD", ok))
    if p.rsi_filter:
        g.append(("RSI", bool(p.rsi_min <= E["rsi"][j] <= p.rsi_max)))
    if p.market_filter and "mkt_ok" in E:
        g.append(("market", bool(E["mkt_ok"][j])))
    if p.in_play_filter:
        g.append(("inplay", bool(close[j] / E["day_open"][j] - 1
                                 >= p.in_play_gain_adr * E["adr_pct"][j]
                                 and E["day_relvol"][j] >= p.in_play_relvol)))
    if p.scanner_filter and "scan_ok" in E:
        g.append(("scanner", bool(E["scan_ok"][j])))
    if p.min_adr_pct > 0:
        g.append(("ADR", bool(p.min_adr_pct <= E["adr_pct"][j] < 90.0)))
    g.append(("surge", bool(surge)))
    g.append(("relvol", bool(E["relvol"][s] >= p.relvol_min)))
    g.append(("pullback", bool(p.min_pullback_bars <= pb_len <= p.max_pullback_bars)))
    g.append(("breakout", bool(high[i] > high[j])))
    minute = int(E["minute"][i])
    g.append(("window", bool(p.entry_start_min <= minute <= p.entry_end_min)))
    return g


def scan_signals(E: dict, p: Params) -> np.ndarray:
    """Return indices of candidate entry bars (bar i breaks high of bar i-1).

    Pattern: surge bar at i-1-L, pullback of L consecutive lower-high bars
    ending at i-1, all conditions evaluated at i-1 (no lookahead), breakout
    confirmed by high[i] > high[i-1].
    """
    high, close, low = E["high"], E["close"], E["low"]
    n = len(high)
    if n < 60:
        return np.empty(0, dtype=np.int64)

    lb = p.momentum_lookback
    gain = np.empty(n)
    gain[:] = np.nan
    gain[lb:] = close[lb:] - close[:-lb]
    surge_raw = (gain >= p.momentum_min_gain_atr * E["atr"]) & (close > E["open"])
    hod_ctx = ((E["hod"] - close) <= p.hod_dist_atr * E["atr"]) \
        & ((close - E["day_open"]) >= p.hod_day_gain_atr * E["atr"])
    if p.momentum_mode == "surge":
        surge = surge_raw
    elif p.momentum_mode == "hod":
        surge = hod_ctx
    else:  # either
        surge = surge_raw | hod_ctx
    surge &= E["relvol"] >= p.relvol_min

    trend = E["ema_fast"] > E["ema_slow"]
    if p.require_above_vwap:
        trend &= close > E["vwap"]
    if p.pullback_hold_ema:
        trend &= low >= E["ema_fast"]
    if p.market_filter and "mkt_ok" in E:
        trend &= E["mkt_ok"]
    if p.in_play_filter:
        day_gain = close / E["day_open"] - 1
        trend &= day_gain >= p.in_play_gain_adr * E["adr_pct"]
        trend &= E["day_relvol"] >= p.in_play_relvol
    if p.min_adr_pct > 0:
        trend &= (E["adr_pct"] >= p.min_adr_pct) & (E["adr_pct"] < 90.0)
    if p.scanner_filter and "scan_ok" in E:
        trend &= E["scan_ok"]
    if p.rsi_filter:
        trend &= (E["rsi"] >= p.rsi_min) & (E["rsi"] <= p.rsi_max)
    if p.macd_filter:
        trend &= E["macd_hist"] > 0
        if p.macd_rising:
            mh = E["macd_hist"]
            rising = np.zeros(n, dtype=bool)
            rising[1:] = mh[1:] > mh[:-1]
            trend &= rising

    L = E["lh_runs"] if p.pullback_def == "lower_high" else E["pb_runs"]
    i = np.arange(n)
    prev = i - 1
    valid = i >= (lb + p.max_pullback_bars + 2)

    pb_len = np.where(valid, L[np.clip(prev, 0, n - 1)], 0)
    pb_ok = (pb_len >= p.min_pullback_bars) & (pb_len <= p.max_pullback_bars)

    surge_idx = np.clip(prev - pb_len, 0, n - 1)
    surge_ok = surge[surge_idx]

    same_day = np.zeros(n, dtype=bool)
    day = E["day"]
    same_day[1:] = day[1:] == day[:-1]
    # entire pattern must be inside one session
    pattern_same_day = day[np.clip(i, 0, n - 1)] == day[np.clip(surge_idx - lb, 0, n - 1)]

    breakout = np.zeros(n, dtype=bool)
    breakout[1:] = high[1:] > high[:-1]

    minute = E["minute"]
    time_ok = (minute >= p.entry_start_min) & (minute <= p.entry_end_min)

    trend_prev = np.zeros(n, dtype=bool)
    trend_prev[1:] = trend[:-1]

    sig = valid & same_day & pattern_same_day & pb_ok & surge_ok & breakout & time_ok & trend_prev
    return np.flatnonzero(sig)
