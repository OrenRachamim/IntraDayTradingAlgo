"""Bar-by-bar trade simulation for Micro Pullback signals.

Conservative fill model:
- entry at max(open, prev_high + 0.01), only if the bar trades through it
- if stop and target are both inside a bar's range -> stop fills first
- trailing stop ratchets on highs, active only after `trail_activate_rr` R
- hard EOD flat at the configured minute's bar close
- costs applied per side (slippage + commission, in bps)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .strategy import Params, scan_signals

TICK = 0.01
COST_BPS_PER_SIDE = 3.0  # 2 bps slippage + 1 bp commission


@dataclass
class Trade:
    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry: float
    exit: float
    ret_pct: float          # net of costs
    bars_held: int
    reason: str             # stop | target | trail | eod
    risk_pct: float = 1.0   # initial stop distance, % of entry (for risk sizing)


def _initial_stop(E: dict, i: int, entry: float, pb_len: int, p: Params) -> float:
    if p.stop_mode == "pullback_low":
        lo = float(np.min(E["low"][i - pb_len - 1: i]))
        stop = lo - TICK
        cap = entry * (1 - p.stop_cap_pct / 100)
        stop = max(stop, cap)
    elif p.stop_mode == "atr":
        stop = entry - p.stop_atr_mult * float(E["atr"][i - 1])
    else:  # pct
        stop = entry * (1 - p.stop_pct / 100)
    return min(stop, entry - TICK)


def _subbar_stop_first(sub: dict, bar_time, stop: float, target: float) -> bool:
    """Resolve an ambiguous coarse bar with 1m data: True if the stop traded first."""
    t0 = bar_time.value
    ts = sub["ts"]
    k0 = np.searchsorted(ts, t0)
    k1 = np.searchsorted(ts, t0 + sub["span_ns"])
    for k in range(k0, k1):
        s_hit = sub["low"][k] <= stop
        t_hit = sub["high"][k] >= target
        if s_hit and t_hit:
            return True   # both inside one minute -> pessimistic
        if s_hit:
            return True
        if t_hit:
            return False
    return True           # no 1m coverage -> pessimistic


def simulate_symbol(symbol: str, E: dict, p: Params, sub: dict | None = None) -> list[Trade]:
    idx = E["index"]
    o, h, l, c = E["open"], E["high"], E["low"], E["close"]
    day, minute, atr_arr = E["day"], E["minute"], E["atr"]
    n = len(o)
    signals = scan_signals(E, p)
    runs = E["lh_runs"] if p.pullback_def == "lower_high" else E["pb_runs"]
    trades: list[Trade] = []
    busy_until = -1  # bar index until which we're in a trade

    for i in signals:
        if i <= busy_until:
            continue
        trigger = h[i - 1] + TICK
        entry = max(o[i], trigger)
        if entry > h[i]:          # never traded through the trigger
            continue
        pb_len = int(runs[i - 1])
        if p.max_retrace_atr < 99:
            depth = h[i - 1 - pb_len] - float(np.min(l[i - pb_len:i]))
            if depth > p.max_retrace_atr * atr_arr[i - 1]:
                continue
        stop = _initial_stop(E, i, entry, pb_len, p)
        risk = entry - stop
        if risk <= 0 or risk / entry > 0.03:
            continue
        if p.target_mode == "rr":
            target = entry + p.target_rr * risk
        else:
            target = entry * (1 + p.target_pct / 100)
        atr_e = float(atr_arr[i - 1])
        trail_armed = False
        hh = entry
        exit_px, reason, j = None, None, i

        for j in range(i, n):
            if day[j] != day[i]:            # safety: never carry overnight
                exit_px, reason = c[j - 1], "eod"
                j -= 1
                break
            hh = max(hh, h[j])
            eff_stop = stop
            if p.trail_mode != "none":
                if not trail_armed and hh >= entry + p.trail_activate_rr * risk:
                    trail_armed = True
                if trail_armed:
                    ts = hh - p.trail_atr_mult * atr_e if p.trail_mode == "atr" \
                        else hh * (1 - p.trail_pct / 100)
                    eff_stop = max(eff_stop, ts)

            entry_bar = (j == i)
            hit_stop = l[j] <= eff_stop
            hit_target = h[j] >= target if not entry_bar else (h[j] >= target and c[j] >= target)
            if hit_stop and hit_target:
                # ambiguous bar: resolve by execution model
                if p.intrabar == "optimistic":
                    hit_stop = False
                elif p.intrabar == "subbar" and sub is not None:
                    hit_stop = _subbar_stop_first(sub, idx[j], eff_stop, target)
                # pessimistic (default): stop first
            if hit_stop:
                exit_px = min(eff_stop, o[j]) if not entry_bar else eff_stop
                reason = "trail" if (trail_armed and eff_stop > stop) else "stop"
                break
            if hit_target:
                exit_px, reason = target, "target"
                break
            if minute[j] >= p.eod_exit_min or j == n - 1:
                exit_px, reason = c[j], "eod"
                break
        if exit_px is None:
            exit_px, reason, j = c[n - 1], "eod", n - 1

        cost = 2 * COST_BPS_PER_SIDE / 1e4
        ret = (exit_px - entry) / entry - cost
        trades.append(Trade(symbol, idx[i], idx[j], round(entry, 4), round(exit_px, 4),
                            ret * 100, j - i, reason, 100.0 * risk / entry))
        busy_until = j
    return trades


def run_portfolio(all_trades: list[Trade], start_equity: float = 100_000.0,
                  max_concurrent: int = 4, sizing_mode: str = "notional",
                  risk_per_trade_pct: float = 1.0,
                  pos_leverage_cap: float = 2.0) -> tuple[pd.Series, pd.DataFrame]:
    """Sequential portfolio simulation.

    sizing_mode:
    - "notional": each trade gets equity/max_concurrent notional.
    - "risk": notional = equity * risk_per_trade_pct / stop_distance_pct, capped at
      pos_leverage_cap * equity (intraday margin). Standard day-trading sizing.

    Returns (daily equity curve, trades df with 'taken' flag).
    """
    if not all_trades:
        return pd.Series(dtype=float), pd.DataFrame()
    df = pd.DataFrame([t.__dict__ for t in all_trades]).sort_values("entry_time").reset_index(drop=True)
    equity = start_equity
    open_exits: list[pd.Timestamp] = []
    taken = np.zeros(len(df), dtype=bool)
    pnl_at: dict[pd.Timestamp, float] = {}

    for k, row in df.iterrows():
        open_exits = [x for x in open_exits if x > row.entry_time]
        if len(open_exits) >= max_concurrent:
            continue
        taken[k] = True
        open_exits.append(row.exit_time)
        if sizing_mode == "risk" and row.risk_pct > 0:
            alloc = equity * (risk_per_trade_pct / row.risk_pct)
            alloc = min(alloc, pos_leverage_cap * equity)
        else:
            alloc = equity / max_concurrent
        pnl = alloc * row.ret_pct / 100
        equity += pnl
        if equity <= 0:            # blown up — stop taking trades
            equity = 0.0
            d = row.exit_time.normalize()
            pnl_at[d] = pnl_at.get(d, 0.0) + pnl
            break
        d = row.exit_time.normalize()
        pnl_at[d] = pnl_at.get(d, 0.0) + pnl

    df["taken"] = taken
    days = sorted(pnl_at)
    eq, vals = start_equity, []
    for d in days:
        eq += pnl_at[d]
        vals.append(eq)
    curve = pd.Series(vals, index=pd.DatetimeIndex(days), name="equity")
    return curve, df
