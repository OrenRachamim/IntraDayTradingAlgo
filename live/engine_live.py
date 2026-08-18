"""Live Micro Pullback engine on IBKR Gateway.

Session flow (all times ET):
  09:2x  start, connect, verify enabled flag, warm up
  10:00  morning scanner -> watchlist (top-K in-play symbols), subscribe 1m bars
  10:01+ on every completed 1m bar: run the validated 1m signal scan on the
         rolling window; on a fresh setup place a stop-entry bracket
         (buy-stop @ prev high + 1c, OCA target/stop children)
  15:30  no new entries
  15:55  flatten everything, cancel all orders
  16:05  daily summary, shutdown

Risk guards, checked before every order: trading_enabled flag (maintenance can
turn it off), daily loss kill-switch, max concurrent positions, max trades/day.
"""
from __future__ import annotations

import math
from datetime import datetime, date

import numpy as np

from engine.indicators import enrich
from engine.strategy import Params, scan_signals

from .broker import Broker
from .config import LiveConfig, hhmm_to_min, now_et, now_et_minute
from .notify import notify, get_logger
from . import state


def build_params(cfg: LiveConfig) -> Params:
    return Params(timeframe="1m", momentum_mode="surge", pullback_def="lower_high",
                  relvol_min=cfg.relvol_min,
                  momentum_min_gain_atr=cfg.momentum_min_gain_atr,
                  stop_mode="pullback_low", stop_cap_pct=cfg.stop_cap_pct,
                  trail_mode="none", target_rr=cfg.target_rr,
                  macd_filter=cfg.require_macd,
                  max_pullback_bars=cfg.max_pullback_bars,
                  in_play_filter=False,     # the live scanner already did this job
                  entry_start_min=hhmm_to_min("10:01"),
                  entry_end_min=hhmm_to_min(cfg.entry_end),
                  eod_exit_min=hhmm_to_min(cfg.eod_flat))


class LiveEngine:
    def __init__(self, cfg: LiveConfig):
        self.cfg = cfg
        self.log = get_logger()
        self.broker = Broker(cfg)
        self.params = build_params(cfg)
        self.watch: dict[str, dict] = {}   # symbol -> {contract, bars, last_len, entry_trades, ttl}
        self.start_equity = 0.0
        self.trades_today = 0
        self.killed = False

    # ---------- risk guards ----------
    def can_enter(self) -> bool:
        if self.killed or not state.trading_enabled():
            return False
        now_min = now_et_minute()
        if now_min >= hhmm_to_min(self.cfg.entry_end):
            return False
        if self.trades_today >= self.cfg.max_trades_per_day:
            return False
        if len(self.broker.open_position_symbols()) >= self.cfg.max_concurrent:
            return False
        return True

    def check_kill_switch(self) -> None:
        if self.killed or self.start_equity <= 0:
            return
        eq = self.broker.equity()
        dd = (eq - self.start_equity) / self.start_equity * 100
        if dd <= -self.cfg.daily_loss_limit_pct:
            self.killed = True
            state.upsert_daily(str(now_et().date()), killed=1)
            notify(self.cfg, f"🛑 KILL SWITCH: day PnL {dd:.2f}% <= "
                             f"-{self.cfg.daily_loss_limit_pct}%. Flattening.", important=True)
            self.broker.flatten_all(reason="kill_switch")

    # ---------- setup detection on the rolling window ----------
    def on_bar_close(self, sym: str) -> None:
        w = self.watch[sym]
        df = self.broker.bars_df(w["bars"])
        if len(df) < 120:
            return
        # only today's session matters for signals; indicators warm on the tail
        tail = df.tail(500)
        E = enrich(tail)
        sigs = scan_signals(E, self.params)
        n = len(tail)
        # a signal at the LAST bar means: this bar broke the prior high -> for
        # live we anticipate at the prior bar instead. So look for a *setup*:
        # conditions true at the last closed bar, entry = its high + 1c.
        setup = len(sigs) > 0 and sigs[-1] >= n - 1
        # expire stale entry orders
        if w["entry_trades"]:
            w["ttl"] -= 1
            filled = any(t.orderStatus.status == "Filled" for t in w["entry_trades"][:1])
            if filled:
                w["entry_trades"] = []
                w["ttl"] = 0
                self.trades_today += 1
                notify(self.cfg, f"✅ {sym} entry filled")
            elif w["ttl"] <= 0:
                self.broker.cancel_trade(w["entry_trades"][0])
                w["entry_trades"] = []
                self.log.info(f"{sym}: entry order expired, cancelled")
            return
        if not setup or not self.can_enter():
            return
        if sym in self.broker.open_position_symbols():
            return
        i = n - 1
        high, low = E["high"], E["low"]
        trigger = float(high[i]) + 0.01
        pb_len = max(int(E["lh_runs"][i]), 1)
        stop = float(np.min(low[max(0, i - pb_len):i + 1])) - 0.01
        stop = max(stop, trigger * (1 - self.params.stop_cap_pct / 100))
        risk_ps = trigger - stop
        if risk_ps <= 0.01 or risk_ps / trigger > 0.03:
            return
        target = trigger + self.params.target_rr * risk_ps
        eq = self.broker.equity()
        qty = int(eq * self.cfg.risk_per_trade_pct / 100 / risk_ps)
        max_notional = eq * self.cfg.pos_leverage_cap / self.cfg.max_concurrent
        qty = min(qty, int(max_notional / trigger))
        if qty < 1:
            return
        trades = self.broker.place_stop_entry_bracket(w["contract"], qty, trigger,
                                                      stop, target)
        w["entry_trades"] = trades
        w["ttl"] = self.cfg.entry_order_ttl_bars
        notify(self.cfg, f"🎯 {sym} setup: buy-stop {qty}@{trigger:.2f} "
                         f"stop {stop:.2f} target {target:.2f} "
                         f"(risk ${qty * risk_ps:.0f})")

    # ---------- session ----------
    def run(self) -> None:
        cfg = self.cfg
        self.broker.connect()
        if not state.trading_enabled():
            notify(cfg, "⚠️ trading disabled by maintenance flag — monitoring only",
                   important=True)
        self.start_equity = self.broker.equity()
        state.upsert_daily(str(now_et().date()), start_equity=self.start_equity)
        notify(cfg, f"▶️ live engine up. equity ${self.start_equity:,.0f}, "
                    f"mode={'PAPER' if cfg.port in (4002, 7497) else 'LIVE'}")

        # wait for scanner time
        scan_min = hhmm_to_min(cfg.scan_time)
        while now_et_minute() < scan_min:
            self.broker.ib.sleep(10)

        from .scanner_live import run_scanner
        picks = run_scanner(self.broker, cfg)
        if not picks:
            notify(cfg, "scanner: no in-play symbols today — standing down")
        for p in picks:
            c = self.broker.stock(p["symbol"])
            bars = self.broker.bars_1m(c, duration="3 D", keep_up_to_date=True)
            self.watch[p["symbol"]] = {"contract": c, "bars": bars,
                                       "last_len": len(bars), "entry_trades": [],
                                       "ttl": 0}
        notify(cfg, "watchlist: " + ", ".join(self.watch) if self.watch else "empty watchlist")

        flat_min = hhmm_to_min(cfg.eod_flat)
        stop_min = hhmm_to_min(cfg.shutdown)
        flattened = False
        while True:
            self.broker.ib.sleep(5)
            self.broker.ensure_connected()
            now_min = now_et_minute()
            if now_min >= stop_min:
                break
            if now_min >= flat_min and not flattened:
                self.broker.flatten_all(reason="eod")
                flattened = True
                notify(cfg, "🏁 EOD flatten complete")
                continue
            if flattened:
                continue
            self.check_kill_switch()
            for sym in list(self.watch):
                w = self.watch[sym]
                if len(w["bars"]) > w["last_len"]:      # a 1m bar completed
                    w["last_len"] = len(w["bars"])
                    try:
                        self.on_bar_close(sym)
                    except Exception as e:  # noqa: BLE001
                        self.log.error(f"{sym} on_bar_close error: {e}")

        eq = self.broker.equity()
        state.upsert_daily(str(now_et().date()), end_equity=eq,
                           realized_pnl=eq - self.start_equity,
                           n_trades=self.trades_today)
        notify(cfg, f"⏹ session done. equity ${eq:,.0f} "
                    f"({(eq - self.start_equity) / self.start_equity * 100:+.2f}% day), "
                    f"{self.trades_today} entries", important=True)
        self.broker.disconnect()
