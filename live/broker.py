"""IBKR Gateway wrapper (ib_async / ib_insync compatible).

Provides: connect with retry, account equity, historical + streaming 1m bars,
native market scanner, stop-entry bracket orders (STP parent + OCA take-profit
limit / protective stop children), cancel-all and flatten-all.
"""
from __future__ import annotations

import asyncio
import time

try:
    from ib_async import IB, Stock, Order, ScannerSubscription, util
except ImportError:  # pragma: no cover - older installs
    from ib_insync import IB, Stock, Order, ScannerSubscription, util

import pandas as pd

from .config import LiveConfig
from .notify import get_logger, notify
from . import state


# IBKR reports connection health through the same channel as real errors.
# These are routine chatter and would drown the log at warning level.
API_INFO_CODES = {2100, 2104, 2106, 2107, 2108, 2119, 2158}
# These mean market data stopped flowing. They are the messages that explain a
# frozen bar feed, and the ones worth waking someone for.
API_FEED_CODES = {1100, 1101, 1102, 2103, 2105, 2110, 2157}


class Broker:
    def __init__(self, cfg: LiveConfig):
        self.cfg = cfg
        self.ib = IB()
        self.log = get_logger()
        self._error_hook = False

    def _watch_api_errors(self) -> None:
        """Record IBKR's own error stream in the operational log.

        ib_async logs to its own logger, which has no handler configured, so
        Python's last-resort handler prints it to stderr and nothing is kept.
        Every explanation IBKR offers for a dead subscription -- a broken data
        farm, a pacing violation, a cancelled query -- was being written to a
        console window and lost. Sessions are diagnosed from the log file, so
        it has to land there.
        """
        if self._error_hook:
            return
        self._error_hook = True

        def on_error(req_id, code, msg, contract=None) -> None:
            what = getattr(contract, "symbol", "") or ""
            line = f"IBKR {code}{' ' + what if what else ''}: {str(msg)[:160]}"
            if code in API_FEED_CODES:
                # a data farm dropping is why bars stop arriving; say so loudly
                notify(self.cfg, f"⚠️ market data link: {line}", important=True)
            elif code in API_INFO_CODES:
                self.log.info(line)
            else:
                self.log.warning(line)

        self.ib.errorEvent += on_error

    # ---------- connection ----------
    def connect(self, retries: int = 12, wait_s: int = 10) -> None:
        self._watch_api_errors()
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

    def sleep(self, seconds: float) -> bool:
        """Sleep while servicing the IB event loop; survive a dropped socket.

        ib.sleep() raises ConnectionError when the link dies. Unguarded, that
        exception ends the trading session outright -- taking the 15:55 flatten
        with it and leaving positions open overnight. The Gateway restarts
        daily by design, so this is an expected event, not an exceptional one.

        Returns False when the connection went down during the sleep.
        """
        try:
            self.ib.sleep(seconds)
            return True
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"⚠️ connection dropped during sleep: {e}")
            try:
                self.ib.disconnect()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(seconds)     # keep the loop's cadence while offline
            return False

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

    def stocks(self, symbols: list[str]) -> dict:
        """Qualify many contracts in one round trip instead of one call each."""
        cs = [Stock(s, "SMART", "USD") for s in symbols]
        try:
            self.ib.qualifyContracts(*cs)
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"qualify batch failed: {e}")
        return {c.symbol: c for c in cs if getattr(c, "conId", 0)}

    SNAPSHOT_TICKS = "165"      # "misc stats", the only source of avVolume
    SNAPSHOT_WAIT_S = 8.0
    SNAPSHOT_CHUNK = 45         # stay clear of the account's market-data lines

    def snapshots(self, contracts: list, chunk: int = 0) -> dict:
        """Market-data snapshots as plain dicts keyed by symbol.

        Carries today's open, the last trade, the previous session's close,
        today's volume so far and the 90-day average daily volume -- enough for
        gap, early move and a cheap relative-volume estimate. Historical bars
        cost one request per symbol against IBKR's ~60-per-10-minutes budget;
        this costs one subscription per symbol with no such limit.

        Generic tick 165 is required: plain `reqTickers` leaves avVolume unset.
        Values are copied out before the subscription is cancelled so the result
        stays valid afterwards.
        """
        chunk = chunk or self.SNAPSHOT_CHUNK
        out = {}
        for i in range(0, len(contracts), chunk):
            batch = contracts[i:i + chunk]
            tickers = []
            try:
                tickers = [self.ib.reqMktData(c, self.SNAPSHOT_TICKS, False, False)
                           for c in batch]
                deadline = time.time() + self.SNAPSHOT_WAIT_S
                while time.time() < deadline:
                    self.ib.sleep(0.5)
                    if all(t.open == t.open and t.close == t.close for t in tickers):
                        break
                for t in tickers:
                    out[t.contract.symbol] = {
                        "open": t.open, "close": t.close, "last": t.last,
                        "market": t.marketPrice(), "volume": t.volume,
                        "avvolume": t.avVolume,
                    }
            except Exception as e:  # noqa: BLE001
                self.log.warning(f"snapshot batch failed: {e}")
            finally:
                for c in batch:
                    try:
                        self.ib.cancelMktData(c)
                    except Exception:  # noqa: BLE001
                        pass
        return out

    def bars_1m(self, contract, duration: str = "2 D", keep_up_to_date: bool = False):
        """1m RTH bars; with keep_up_to_date=True the list live-updates in place."""
        return self.ib.reqHistoricalData(
            contract, endDateTime="", durationStr=duration, barSizeSetting="1 min",
            whatToShow="TRADES", useRTH=True, formatDate=2,
            keepUpToDate=keep_up_to_date)

    HIST_CONCURRENCY = 4      # IBKR serialises historical requests internally
    HIST_TIMEOUT_S = 120.0

    def bars_1m_many(self, contracts: list, duration: str = "12 D") -> dict:
        """1m bars for several contracts, keyed by symbol; empty results dropped.

        A 12-day 1m request takes several seconds, so a dozen of them in series
        dominates the morning scan. They are issued a few at a time instead:
        IBKR queues historical requests internally, so firing all of them at
        once just makes them time out -- and a timeout comes back as an EMPTY
        list, not an error, which would silently corrupt any metric computed
        from it. Symbols that return nothing are therefore dropped here, and
        the caller must treat a missing key as "no data", never as a default.
        """
        if not contracts:
            return {}

        async def fetch(sem, c):
            async with sem:
                return await self.ib.reqHistoricalDataAsync(
                    c, endDateTime="", durationStr=duration,
                    barSizeSetting="1 min", whatToShow="TRADES", useRTH=True,
                    formatDate=2, timeout=self.HIST_TIMEOUT_S)

        async def gather_all():
            sem = asyncio.Semaphore(self.HIST_CONCURRENCY)
            return await asyncio.gather(*(fetch(sem, c) for c in contracts),
                                        return_exceptions=True)

        try:
            results = self.ib.run(gather_all())
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"parallel history failed ({e}); fetching serially")
            results = [self.bars_1m(c, duration=duration) for c in contracts]

        out, empty = {}, []
        for c, r in zip(contracts, results):
            if isinstance(r, BaseException):
                self.log.warning(f"history {c.symbol} failed: {r}")
            elif not r:
                empty.append(c.symbol)
            else:
                out[c.symbol] = r
        if empty:
            self.log.warning(f"⚠️ no history returned for {len(empty)} symbols "
                             f"({', '.join(empty)}) — excluded, not defaulted")
        return out

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
