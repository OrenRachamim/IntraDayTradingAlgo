"""Portfolio-level daily backtester for VCP breakout setups.

Event order within a day (conservative, no look-ahead):
  1. scheduled exits (trail-MA / time-stop signals from yesterday) at the open
  2. protective stops (gap-through fills at the open)
  3. profit targets (sell into strength)
  4. new entries via buy-stop at the pivot trigger (gates checked on yesterday's data)
  5. end-of-day: mark to market, raise stops (breakeven), flag trail/time exits
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .data import Market, SymbolData
from .indicators import sma
from .vcp_detector import Setup


@dataclass
class Trade:
    symbol: str
    entry_idx: int
    entry_date: pd.Timestamp
    entry_price: float          # includes slippage
    shares: int
    init_stop: float
    pivot: float
    exit_idx: int = -1
    exit_date: pd.Timestamp | None = None
    exit_price: float = math.nan
    reason: str = ""
    pnl: float = math.nan       # net of costs
    r_multiple: float = math.nan


@dataclass
class _Position:
    trade: Trade
    stop: float
    init_risk: float            # per share
    target: float               # 0 = off
    breakeven_done: bool = False
    trail_armed: bool = False
    exit_at_open: str = ""      # reason string when an exit is scheduled
    last_mark: float = 0.0


@dataclass
class _ActiveSetup:
    setup: Setup
    activated_idx: int


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: list[Trade]
    daily_exposure: pd.Series
    n_setups: int = 0
    n_triggered: int = 0


class Backtester:
    def __init__(self, cfg: Config, calendar: pd.DatetimeIndex,
                 data: dict[str, SymbolData], setups: dict[str, list[Setup]],
                 tt_masks: dict[str, np.ndarray], liq_masks: dict[str, np.ndarray],
                 rs_pct: dict[str, np.ndarray], market: Market, start_idx: int):
        self.cfg = cfg
        self.cal = calendar
        self.data = data
        self.tt = tt_masks
        self.liq = liq_masks
        self.rs = rs_pct
        self.market = market
        self.start_idx = start_idx
        self._trail_cache: dict[str, np.ndarray] = {}
        self._volsma_cache: dict[str, np.ndarray] = {}
        # setups indexed by confirmation bar for fast activation
        self.setups_by_day: dict[int, list[Setup]] = {}
        self.n_setups = 0
        for sym_setups in setups.values():
            for s in sym_setups:
                self.setups_by_day.setdefault(s.confirm_idx, []).append(s)
                self.n_setups += 1

    def _trail_sma(self, symbol: str) -> np.ndarray | None:
        w = self.cfg.exit.trail_ma
        if w <= 0:
            return None
        if symbol not in self._trail_cache:
            self._trail_cache[symbol] = sma(self.data[symbol].close.astype(np.float64), w)
        return self._trail_cache[symbol]

    def _vol_sma50(self, symbol: str) -> np.ndarray:
        if symbol not in self._volsma_cache:
            self._volsma_cache[symbol] = sma(self.data[symbol].volume.astype(np.float64), 50)
        return self._volsma_cache[symbol]

    # ---- accounting helpers -------------------------------------------------
    def _buy_cost(self, price: float) -> float:
        c = self.cfg.costs
        return price * (1 + c.slippage_bps / 1e4) * (1 + c.commission_bps / 1e4)

    def _sell_net(self, price: float) -> float:
        c = self.cfg.costs
        return price * (1 - c.slippage_bps / 1e4) * (1 - c.commission_bps / 1e4)

    def _close_position(self, pos: _Position, t: int, price: float, reason: str) -> float:
        tr = pos.trade
        net = self._sell_net(price)
        tr.exit_idx = t
        tr.exit_date = self.cal[t]
        tr.exit_price = net
        tr.reason = reason
        tr.pnl = (net - tr.entry_price) * tr.shares
        tr.r_multiple = (net - tr.entry_price) / pos.init_risk if pos.init_risk > 0 else math.nan
        return net * tr.shares

    # ---- main loop ----------------------------------------------------------
    def run(self) -> BacktestResult:
        cfg = self.cfg
        cash = cfg.backtest.initial_capital
        positions: dict[str, _Position] = {}
        active: dict[str, _ActiveSetup] = {}
        trades: list[Trade] = []
        n_days = len(self.cal)
        equity_curve = np.zeros(n_days - self.start_idx)
        exposure = np.zeros(n_days - self.start_idx)
        n_triggered = 0

        for t in range(self.start_idx, n_days):
            # ---------- 1-3: manage open positions ----------
            for sym in list(positions):
                pos = positions[sym]
                sd = self.data[sym]
                tr = pos.trade
                if t > sd.last_idx:  # symbol stopped trading -> force close at last mark
                    cash += self._close_position(pos, t, pos.last_mark, "delisted")
                    trades.append(tr)
                    del positions[sym]
                    continue
                o, h, l, c = (float(sd.open[t]), float(sd.high[t]),
                              float(sd.low[t]), float(sd.close[t]))
                if math.isnan(o) or math.isnan(l):
                    continue  # halted day, keep position at last mark
                exited = False
                if pos.exit_at_open:
                    cash += self._close_position(pos, t, o, pos.exit_at_open)
                    exited = True
                elif o <= pos.stop:
                    cash += self._close_position(pos, t, o, "stop_gap")
                    exited = True
                elif l <= pos.stop:
                    cash += self._close_position(pos, t, pos.stop, "stop")
                    exited = True
                elif pos.target > 0:
                    if o >= pos.target:
                        cash += self._close_position(pos, t, o, "target_gap")
                        exited = True
                    elif h >= pos.target:
                        cash += self._close_position(pos, t, pos.target, "target")
                        exited = True
                if exited:
                    trades.append(tr)
                    del positions[sym]
                    continue
                if not math.isnan(c):
                    pos.last_mark = c

            # ---------- 4: entries ----------
            # activate setups confirmed on this bar (monitored from next bar)
            for s in self.setups_by_day.get(t, []):
                active[s.symbol] = _ActiveSetup(s, t)

            candidates: list[tuple[float, str, _ActiveSetup]] = []
            for sym in list(active):
                a = active[sym]
                if t <= a.activated_idx:
                    continue
                sd = self.data[sym]
                s = a.setup
                if (t > sd.last_idx
                        or t - a.activated_idx > cfg.vcp.setup_max_active_days):
                    del active[sym]
                    continue
                c_prev = float(sd.close[t - 1]) if t - 1 >= 0 else math.nan
                if not math.isnan(c_prev) and c_prev < s.support_low:
                    del active[sym]  # support broken -> pattern failed
                    continue
                if sym in positions:
                    continue
                # gates evaluated on yesterday's completed bar
                if not (self.tt[sym][t - 1] and self.liq[sym][t - 1]):
                    continue
                if cfg.entry.market_filter and not self.market.regime_ok[t - 1]:
                    continue
                o, h = float(sd.open[t]), float(sd.high[t])
                if math.isnan(o) or math.isnan(h):
                    continue
                trigger = s.pivot * (1 + cfg.entry.breakout_buffer)
                fill = math.nan
                if o >= trigger:
                    fill = o
                elif h >= trigger:
                    fill = trigger
                if math.isnan(fill):
                    continue
                if fill > s.pivot * (1 + cfg.entry.max_chase_pct):
                    del active[sym]  # gapped too far above pivot: chase guard
                    continue
                if cfg.entry.bo_vol_mult > 0:
                    volsma = self._vol_sma50(sym)[t - 1]
                    if math.isnan(volsma) or float(sd.volume[t]) < cfg.entry.bo_vol_mult * volsma:
                        continue
                if cfg.entry.rank_by == "tightness":
                    rank = -s.depths[-1]        # tighter final contraction first
                else:
                    rank = float(self.rs[sym][t - 1]) if not math.isnan(self.rs[sym][t - 1]) else 0.0
                candidates.append((rank, sym, a))

            if candidates:
                candidates.sort(key=lambda x: -x[0])
                # equity for sizing = cash + current marks
                mark_value = sum(p.last_mark * p.trade.shares for p in positions.values())
                equity_now = cash + mark_value
                for rs_rank, sym, a in candidates:
                    if len(positions) >= cfg.risk.max_positions:
                        break
                    s = a.setup
                    sd = self.data[sym]
                    o, h = float(sd.open[t]), float(sd.high[t])
                    trigger = s.pivot * (1 + cfg.entry.breakout_buffer)
                    fill = o if o >= trigger else trigger
                    exec_price = self._buy_cost(fill)
                    stop = fill * (1 - cfg.risk.stop_pct)
                    if cfg.risk.stop_use_contraction_low and s.support_low > stop:
                        stop = s.support_low        # tighter stop under support
                    stop = max(stop, fill * (1 - cfg.risk.stop_max_pct))
                    risk_ps = exec_price - stop
                    if risk_ps <= 0:
                        continue
                    shares = int(min(
                        equity_now * cfg.risk.risk_per_trade / risk_ps,
                        equity_now * cfg.risk.max_weight / exec_price,
                        cash / exec_price,
                    ))
                    if shares < 1 or shares * exec_price < 500:
                        continue
                    cash -= shares * exec_price
                    init_risk = exec_price - stop
                    target = (exec_price + cfg.exit.target_R * init_risk
                              if cfg.exit.target_R > 0 else 0.0)
                    tr = Trade(symbol=sym, entry_idx=t, entry_date=self.cal[t],
                               entry_price=exec_price, shares=shares,
                               init_stop=stop, pivot=s.pivot)
                    positions[sym] = _Position(trade=tr, stop=stop, init_risk=init_risk,
                                               target=target, last_mark=fill)
                    del active[sym]
                    n_triggered += 1

            # ---------- 5: end of day ----------
            for sym, pos in positions.items():
                sd = self.data[sym]
                if t > sd.last_idx:
                    continue
                c = float(sd.close[t])
                h = float(sd.high[t])
                if math.isnan(c):
                    continue
                tr = pos.trade
                if (cfg.exit.breakeven_at_R > 0 and not pos.breakeven_done
                        and h >= tr.entry_price + cfg.exit.breakeven_at_R * pos.init_risk):
                    pos.stop = max(pos.stop, tr.entry_price)   # effective from tomorrow
                    pos.breakeven_done = True
                act_R = cfg.exit.trail_activation_R
                if act_R > 0 and not pos.trail_armed:
                    if h >= tr.entry_price + act_R * pos.init_risk:
                        pos.trail_armed = True
                trail = self._trail_sma(sym)
                if (trail is not None and not math.isnan(trail[t]) and c < trail[t]
                        and t > tr.entry_idx
                        and (act_R <= 0 or pos.trail_armed)):
                    pos.exit_at_open = "trail_ma"
                if (cfg.exit.time_stop_days > 0 and not pos.exit_at_open
                        and t - tr.entry_idx >= cfg.exit.time_stop_days
                        and c < tr.entry_price + pos.init_risk):
                    pos.exit_at_open = "time_stop"

            mark_value = sum(p.last_mark * p.trade.shares for p in positions.values())
            equity_curve[t - self.start_idx] = cash + mark_value
            exposure[t - self.start_idx] = mark_value / max(cash + mark_value, 1e-9)

        # close anything still open at the last bar
        t = n_days - 1
        for sym, pos in list(positions.items()):
            cash += self._close_position(pos, t, pos.last_mark, "end_of_test")
            trades.append(pos.trade)
            del positions[sym]
        equity_curve[-1] = cash

        idx = self.cal[self.start_idx:]
        return BacktestResult(
            equity=pd.Series(equity_curve, index=idx, name="equity"),
            trades=trades,
            daily_exposure=pd.Series(exposure, index=idx, name="exposure"),
            n_setups=self.n_setups,
            n_triggered=n_triggered,
        )
