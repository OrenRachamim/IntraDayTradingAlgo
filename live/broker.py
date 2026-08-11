"""IBKR Gateway wrapper (ib_async / ib_insync compatible).

Provides: connect with retry, account equity, historical + streaming 1m bars,
native market scanner, stop-entry bracket orders (STP parent + OCA take-profit
limit / protective stop children), cancel-all and flatten-all.
"""
from __future__ import annotations

import time

try:
    from ib_async import IB, Stock, Order, ScannerSubscription, util
except ImportError:  # pragma: no cover - older installs
    from ib_insync import IB, Stock, Order, ScannerSubscription, util

import pandas as pd

from .config import LiveConfig
from .notify import get_logger, notify
from . import state


class Broker:
    def __init__(self, cfg: LiveConfig):
        self.cfg = cfg
        self.ib = IB()
        self.log = get_logger()

    # ---------- connection ----------
    def connect(self, retries: int = 12, wait_s: int = 10) -> None:
        for k in range(retries):
            try:
                self.ib.connect(self.cfg.host, self.cfg.port,
                                clientId=self.cfg.client_id, timeout=20)
                mode = "PAPER" if self.cfg.port in (4002, 7497) else "LIVE"
                self.log.info(f"connected to IB Gateway {self.cfg.host}:{self.cfg.port} "
                              f"({mode}, clientId={self.cfg.client_id})")
                return
            except Exception as e:  # noqa: BLE001
                self.log.warning(f"connect attempt {k + 1}/{retries} failed: {e}")
                time.sleep(wait_s)
        raise ConnectionError("could not connect to IB Gateway")

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    def ensure_connected(self) -> None:
        if not self.ib.isConnected():
            self.log.warning("gateway connection lost — reconnecting")
            self.connect()

    # ---------- account ----------
    def equity(self) -> float:
        for row in self.ib.accountSummary(self.cfg.account or ""):
            if row.tag == "NetLiquidation":
                return float(row.value)
        raise RuntimeError("NetLiquidation not found in account summary")

    def open_position_symbols(self) -> set[str]:
        return {p.contract.symbol for p in self.ib.positions() if p.position != 0}

    # ---------- contracts & data ----------
    def stock(self, symbol: str) -> Stock:
        c = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(c)
        return c

    def bars_1m(self, contract, duration: str = "2 D", keep_up_to_date: bool = False):
        """1m RTH bars; with keep_up_to_date=True the list live-updates in place."""
        return self.ib.reqHistoricalData(
            contract, endDateTime="", durationStr=duration, barSizeSetting="1 min",
            whatToShow="TRADES", useRTH=True, formatDate=2,
            keepUpToDate=keep_up_to_date)

    @staticmethod
    def bars_df(bars) -> pd.DataFrame:
        df = util.df(bars)
        if df is None or not len(df):
            return pd.DataFrame()
        df = df.rename(columns={"date": "Date", "open": "Open", "high": "High",
                                "low": "Low", "close": "Close", "volume": "Volume"})
        df = df.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]
        df.index = pd.DatetimeIndex(df.index).tz_convert("US/Eastern")
        return df

    def prev_close(self, contract) -> float:
        bars = self.ib.reqHistoricalData(
            contract, endDateTime="", durationStr="3 D", barSizeSetting="1 day",
            whatToShow="TRADES", useRTH=True, formatDate=2)
        if len(bars) >= 2:
            return float(bars[-2].close)
        return float("nan")

    # ---------- native market scanner ----------
    def scan_candidates(self, max_rows: int = 40) -> list[str]:
        """Union of IBKR top-percent-gainers and most-active US stocks."""
        out: list[str] = []
        for code in ("TOP_PERC_GAIN", "MOST_ACTIVE"):
            sub = ScannerSubscription(instrument="STK", locationCode="STK.US.MAJOR",
                                      scanCode=code, abovePrice=self.cfg.min_price,
                                      belowPrice=self.cfg.max_price,
                                      aboveVolume=self.cfg.min_day_volume,
                                      numberOfRows=max_rows)
            try:
                rows = self.ib.reqScannerData(sub, [], [])
                out += [r.contractDetails.contract.symbol for r in rows]
            except Exception as e:  # noqa: BLE001
                self.log.warning(f"scanner {code} failed: {e}")
        seen, uniq = set(), []
        for s in out:
            if s not in seen and s.isalpha():
                seen.add(s)
                uniq.append(s)
        return uniq

    # ---------- orders ----------
    def place_stop_entry_bracket(self, contract, qty: int, trigger: float,
                                 stop: float, target: float):
        """Buy-stop entry; on fill an OCA pair (target limit / protective stop)."""
        oca = f"mp_{contract.symbol}_{int(time.time())}"
        parent = Order(action="BUY", orderType="STP", totalQuantity=qty,
                       auxPrice=round(trigger, 2), tif="DAY", transmit=False,
                       outsideRth=False)
        tp = Order(action="SELL", orderType="LMT", totalQuantity=qty,
                   lmtPrice=round(target, 2), tif="DAY", ocaGroup=oca, ocaType=1,
                   transmit=False)
        sl = Order(action="SELL", orderType="STP", totalQuantity=qty,
                   auxPrice=round(stop, 2), tif="DAY", ocaGroup=oca, ocaType=1,
                   transmit=True)
        trades = []
        for o in (parent, tp, sl):
            if o is not parent:
                o.parentId = parent.orderId
            trades.append(self.ib.placeOrder(contract, o))
            self.ib.sleep(0.05)
        state.log_order(contract.symbol, "BUY", qty, "STP", trigger,
                        trades[0].order.orderId, "submitted",
                        f"bracket stop={stop:.2f} target={target:.2f}")
        return trades

    def cancel_trade(self, trade) -> None:
        try:
            if trade.orderStatus.status not in ("Filled", "Cancelled", "ApiCancelled"):
                self.ib.cancelOrder(trade.order)
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"cancel failed: {e}")

    def cancel_all_open(self) -> None:
        for t in list(self.ib.openTrades()):
            self.cancel_trade(t)
        self.ib.sleep(1)

    def flatten_all(self, reason: str = "eod") -> None:
        """Cancel every working order, then market-out of every position."""
        self.cancel_all_open()
        for p in self.ib.positions():
            if p.position == 0:
                continue
            action = "SELL" if p.position > 0 else "BUY"
            c = p.contract
            c.exchange = "SMART"
            o = Order(action=action, orderType="MKT",
                      totalQuantity=abs(int(p.position)), tif="DAY")
            self.ib.placeOrder(c, o)
            state.log_order(c.symbol, action, abs(int(p.position)), "MKT", 0.0,
                            o.orderId, "submitted", f"flatten:{reason}")
        self.ib.sleep(2)
        left = self.open_position_symbols()
        if left:
            notify(self.cfg, f"⚠️ positions still open after flatten: {left}", important=True)
