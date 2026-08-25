#!/usr/bin/env python3
"""Read-only console dashboard for the live engine.

  python -m live.monitor            # live dashboard, refreshes every 2s
  python -m live.monitor --once     # render a single frame and exit
  python -m live.monitor --rows 20  # taller log pane

Reads the SQLite state DB (read-only) and the rotating log. With --ib it also
opens its own read-only IB connection on a separate clientId for prices,
positions and the broker's daily P&L; it never places, changes or cancels an
order.

The governing rule is that no figure may claim more than it knows:

* "positions" says UNKNOWN, never "flat", when the broker is unreachable.
* "closed trades" says so plainly, because the engine never writes the trades
  table -- a zero there would read as "nothing closed yet" rather than
  "not measured".
* "equity @start" is the session baseline, rewritten on every engine restart,
  so it is not the market open and is not labelled as such. Day P&L comes from
  the broker, which survives restarts.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from rich import box
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import LiveConfig, STATE_DIR, hhmm_to_min, load_config, now_et

DB_PATH = os.path.join(STATE_DIR, "live.db")
LOG_PATH = os.path.join(STATE_DIR, "logs", "live.log")


# ---------- data sources (all read-only) ----------

def query(sql: str, params: tuple = ()) -> list[tuple]:
    """Read-only DB query; returns [] when the DB is missing, locked or broken."""
    if not os.path.exists(DB_PATH):
        return []
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []


def safe(text: str) -> str:
    """Drop characters the console cannot encode (Windows legacy codepages).

    The log carries emoji; the dashboard conveys the same meaning with colour,
    so losing them costs nothing and beats a UnicodeEncodeError mid-render.
    """
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        return text.encode(enc, errors="replace").decode(enc, errors="replace")
    except (LookupError, UnicodeError):
        return text.encode("ascii", errors="replace").decode("ascii")


def log_tail(n: int) -> list[str]:
    """Last n lines of the log, without reading the whole file."""
    if not os.path.exists(LOG_PATH):
        return []
    try:
        with open(LOG_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 32768))
            data = f.read().decode("utf-8", errors="replace")
        return data.splitlines()[-n:]
    except OSError:
        return []


RESCAN_S = 15.0
_proc_cache: dict = {"proc": None, "ts": 0.0}


def engine_proc():
    """The running live.run_live process, or None.

    Scanning every process for its cmdline costs ~2s on Windows -- far too slow
    to repeat every refresh -- so the handle is cached. is_running() is cheap
    and PID-reuse safe, and a death forces an immediate rescan so an engine
    restart shows up at once.
    """
    try:
        import psutil
    except ImportError:
        return None

    p = _proc_cache["proc"]
    if p is not None:
        try:
            if p.is_running():
                return p
        except psutil.Error:
            pass
        _proc_cache.update(proc=None, ts=0.0)   # died -> rescan now

    if time.time() - _proc_cache["ts"] < RESCAN_S:
        return None                             # scanned recently, none found
    _proc_cache["ts"] = time.time()

    for proc in psutil.process_iter(["cmdline", "create_time"]):
        try:
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "live.run_live" in cmd and "monitor" not in cmd:
                _proc_cache["proc"] = proc
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


# ---------- optional live broker feed ----------

class IBFeed:
    """Read-only IB connection for prices, positions and equity.

    Runs on its own clientId (`monitor_client_id`) so it cannot collide with the
    trading session -- IBKR allows many concurrent API clients on one account.
    This class only ever reads: it places, modifies and cancels nothing.

    Market data starts at type 1 (live) and falls back to 3 (delayed) when the
    account has no realtime subscription for the symbol.
    """

    RETRY_S = 20.0
    FALLBACK_AFTER_S = 6.0

    def __init__(self, cfg: LiveConfig):
        self.cfg = cfg
        self.ib = None
        self.status = Text("off", style="dim")
        self._contracts: dict = {}
        self._tickers: dict = {}
        self._bars: dict = {}
        self._gates: dict = {}
        self._bar_age: dict = {}
        self._pnl = None
        self._backfilled = False
        self._next_try = 0.0
        self._subscribed_at = 0.0
        self._delayed = False

    def ensure(self) -> bool:
        try:
            if self.ib is not None and self.ib.isConnected():
                return True
        except Exception:  # noqa: BLE001 - a half-dead handle is still dead
            self.ib = None
        if time.time() < self._next_try:
            return False
        self._next_try = time.time() + self.RETRY_S
        try:
            from ib_async import IB
        except ImportError:
            self.status = Text("ib_async not installed", style="red")
            return False
        # A second dashboard would collide on the configured id, so step to the
        # next free one instead of sitting there showing "offline".
        last = ""
        for offset in range(4):
            cid = self.cfg.monitor_client_id + offset
            try:
                ib = IB()
                ib.connect(self.cfg.host, self.cfg.port, clientId=cid, timeout=8)
                ib.reqMarketDataType(1)
                self.ib, self._delayed = ib, False
                self.status = Text(f"live (clientId {cid})", style="green")
                return True
            except Exception as e:  # noqa: BLE001 - the dashboard must never die
                last = str(e) or e.__class__.__name__
        self.ib = None
        self.status = Text(f"offline: {last[:38]}", style="red")
        return False

    def contract(self, symbol: str):
        """Qualified contract, or None if the broker could not confirm it."""
        if symbol not in self._contracts:
            from ib_async import Stock
            c = Stock(symbol, "SMART", "USD")
            try:
                self.ib.qualifyContracts(c)
            except Exception:  # noqa: BLE001
                return None
            self._contracts[symbol] = c
        return self._contracts[symbol]

    def watch(self, symbols: list[str]) -> None:
        """Subscribe to streaming quotes for any symbol not already subscribed."""
        if not self.ensure():
            return
        for s in symbols:
            if s in self._tickers:
                continue
            c = self.contract(s)
            if c is None:
                continue
            try:
                self._tickers[s] = self.ib.reqMktData(c, "", False, False)
                self._subscribed_at = self._subscribed_at or time.time()
            except Exception:  # noqa: BLE001
                continue
        # no realtime entitlement -> switch this client to delayed data
        if self._tickers and not self._delayed and self._subscribed_at \
                and time.time() - self._subscribed_at > self.FALLBACK_AFTER_S \
                and all(not _num(t.last) and not _num(t.close)
                        for t in self._tickers.values()):
            try:
                self.ib.reqMarketDataType(3)
                self._delayed = True
                self.status = Text(f"delayed (clientId {self.cfg.monitor_client_id})",
                                   style="yellow")
            except Exception:  # noqa: BLE001
                pass

    def quote(self, symbol: str) -> tuple[float, float]:
        """(last price, day change fraction); nan when unavailable."""
        t = self._tickers.get(symbol)
        if t is None:
            return float("nan"), float("nan")
        last = next((v for v in (t.last, t.close, t.marketPrice()) if _num(v)),
                    float("nan"))
        prev = t.close if _num(t.close) else float("nan")
        chg = (last / prev - 1) if _num(last) and _num(prev) and prev else float("nan")
        return last, chg

    def gates(self, symbol: str):
        """Entry-condition status for the latest closed 1m bar, or None.

        Subscribes once per symbol with keepUpToDate, so the bar list refreshes
        itself and no further historical requests are made -- the dashboard must
        not eat into the engine's IBKR pacing budget.
        """
        if not self.ensure():
            return None
        bars = self._bars.get(symbol)
        if bars is None:
            try:
                bars = self.ib.reqHistoricalData(
                    self.contract(symbol) or symbol, endDateTime="", durationStr="2 D",
                    barSizeSetting="1 min", whatToShow="TRADES", useRTH=True,
                    formatDate=2, keepUpToDate=True)
            except Exception:  # noqa: BLE001
                return None
            self._bars[symbol] = bars
        if len(bars) < 60:
            return None
        self._bar_age[symbol] = self._age_of(bars)
        cached = self._gates.get(symbol)
        if cached and cached[0] == len(bars):
            return cached[1]
        try:
            from engine.indicators import enrich
            from engine.strategy import entry_gates
            from .broker import Broker
            from .engine_live import build_params
            df = Broker.bars_df(bars)
            if len(df) < 60:
                return None
            E = enrich(df.tail(500))
            got = entry_gates(E, build_params(self.cfg), len(E["high"]) - 1)
        except Exception:  # noqa: BLE001
            return None
        self._gates[symbol] = (len(bars), got)
        return got

    @staticmethod
    def _age_of(bars) -> float:
        """Seconds since the newest bar's own timestamp, or nan."""
        try:
            t = bars[-1].date
            if isinstance(t, datetime):
                if t.tzinfo is None:
                    t = t.replace(tzinfo=ZoneInfo("UTC"))
                return (datetime.now(ZoneInfo("UTC")) - t).total_seconds()
        except (IndexError, AttributeError, TypeError):
            pass
        return float("nan")

    def bar_age(self, symbol: str) -> float:
        """Age of this symbol's newest bar in seconds; nan when unknown.

        Read from the bar's own timestamp rather than when it arrived, so a
        subscription that silently stops delivering shows up as an ageing bar
        instead of looking healthy.
        """
        return self._bar_age.get(symbol, float("nan"))

    def portfolio(self):
        """Open positions, or None when the broker could not be reached.

        None and [] must stay distinguishable: [] means genuinely flat, None
        means unknown -- reporting "flat" on a dead connection would be a lie
        about the one thing the operator most needs to trust.
        """
        if not self.ensure():
            return None
        try:
            return [p for p in self.ib.portfolio() if p.position]
        except Exception:  # noqa: BLE001
            return None

    def fills(self) -> list[dict]:
        """Today's executions from the broker, newest first.

        The engine logs only the parent buy-stop and never the OCA children, so
        exits exist nowhere in local state -- the broker is the only source that
        has them. Backfilled once with reqExecutions; after that ib.fills() is
        kept current by execution events, so this costs nothing per refresh.
        """
        if not self.ensure():
            return []
        try:
            if not self._backfilled:
                from ib_async import ExecutionFilter
                self.ib.reqExecutions(ExecutionFilter())
                self._backfilled = True
            raw = self.ib.fills()
        except Exception:  # noqa: BLE001
            return []

        # one execution becomes many partial fills; group them back per order
        agg: dict = {}
        for f in raw:
            e, c = f.execution, f.contract
            key = (e.orderId, e.side, c.symbol)
            a = agg.setdefault(key, {"symbol": c.symbol, "side": e.side,
                                     "order_id": e.orderId, "shares": 0.0,
                                     "notional": 0.0, "time": e.time})
            a["shares"] += e.shares
            a["notional"] += e.shares * e.price
            a["time"] = max(a["time"], e.time)
        out = []
        for a in agg.values():
            if a["shares"] <= 0:
                continue
            a["avg"] = a["notional"] / a["shares"]
            out.append(a)
        out.sort(key=lambda r: r["time"], reverse=True)

        # attach realised P&L to a closing leg once both sides are complete
        for r in out:
            if r["side"] != "SLD":
                continue
            bought = sum(x["notional"] for x in out
                         if x["symbol"] == r["symbol"] and x["side"] == "BOT")
            shares = sum(x["shares"] for x in out
                         if x["symbol"] == r["symbol"] and x["side"] == "BOT")
            if shares and abs(shares - r["shares"]) < 1:
                r["pnl"] = r["notional"] - bought
        return out

    def day_pnl(self):
        """The broker's own daily P&L, or None. Authoritative: it survives an
        engine restart, which any equity-vs-session-start figure does not."""
        if not self.ensure():
            return None
        try:
            if self._pnl is None:
                acct = self.cfg.account or (self.ib.managedAccounts() or [""])[0]
                if not acct:
                    return None
                self._pnl = self.ib.reqPnL(acct)
                self.ib.sleep(1.0)
            v = self._pnl.dailyPnL
            return v if v == v else None
        except Exception:  # noqa: BLE001
            return None

    def equity(self) -> float:
        if not self.ensure():
            return float("nan")
        try:
            for row in self.ib.accountSummary(self.cfg.account or ""):
                if row.tag == "NetLiquidation":
                    return float(row.value)
        except Exception:  # noqa: BLE001
            pass
        return float("nan")

    def drop(self, why: str) -> None:
        """Forget a dead connection so ensure() can rebuild it.

        The cached tickers, bars and PnL handle all belong to the old socket;
        keeping them across a reconnect would leave the dashboard quietly
        showing values frozen at the moment the link died.
        """
        try:
            if self.ib is not None:
                self.ib.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.ib = None
        self._tickers.clear()
        self._bars.clear()
        self._gates.clear()
        self._pnl = None
        self._backfilled = False
        self._subscribed_at = 0.0
        self._next_try = time.time() + 3.0
        self.status = Text(why, style="bold red")

    def pump(self, seconds: float) -> None:
        """Sleep while keeping the IB event loop alive.

        A dropped socket surfaces here as ConnectionError out of ib.sleep().
        The dashboard has to outlive its broker connection -- an operator
        losing the whole screen because the Gateway restarted is worse than
        losing the prices -- so the feed is marked down and retried later.
        """
        ib = self.ib
        if ib is not None:
            try:
                if ib.isConnected():
                    ib.sleep(seconds)
                    return
            except Exception as e:  # noqa: BLE001
                self.drop(f"connection lost ({type(e).__name__})")
        time.sleep(seconds)

    def close(self) -> None:
        try:
            if self.ib is not None and self.ib.isConnected():
                self.ib.disconnect()
        except Exception:  # noqa: BLE001
            pass


def _num(v) -> bool:
    return v is not None and isinstance(v, (int, float)) and v == v and v > 0


# ---------- fill alerts ----------

class FillAlerter:
    """Push a Telegram message for every completed broker fill.

    This lives in the dashboard rather than the engine on purpose: the engine
    only ever observes its own entry order, because the exits are OCA children
    it hands to IBKR and never watches again. The broker's execution stream is
    the only place both sides of a trade appear, and the dashboard is already
    reading it.

    Two things it must not do. It must not announce the backlog: the first poll
    of a session backfills every execution of the day, and firing a message for
    each would bury the one that matters. And it must not announce an order
    twice -- fills arrive in partials, so the aggregate grows between refreshes;
    an order is only reported once its size has stopped changing.
    """

    def __init__(self, cfg: LiveConfig):
        self.cfg = cfg
        self.sent = 0
        self.failed = 0
        self._seen: set = set()
        self._sizes: dict = {}
        self._primed = False

    @property
    def armed(self) -> bool:
        from .notify import telegram_configured
        return telegram_configured(self.cfg)

    def poll(self, fills: list[dict]) -> list[dict]:
        """Announce newly completed fills; returns the ones sent."""
        current = {(f.get("order_id"), f["side"]): f for f in fills}

        if not self._primed:            # adopt the session's history silently
            self._seen = set(current)
            self._sizes = {k: f["shares"] for k, f in current.items()}
            self._primed = True
            return []

        announced = []
        for key, f in current.items():
            previous = self._sizes.get(key)
            self._sizes[key] = f["shares"]
            if key in self._seen:
                continue
            if previous is None or abs(previous - f["shares"]) > 1e-9:
                continue                # still filling; wait for it to settle
            self._seen.add(key)
            announced.append(f)
            self._send(f)
        return announced

    def _send(self, f: dict) -> None:
        verb = "BUY" if f["side"] == "BOT" else "SELL"
        mark = "🟢" if f["side"] == "BOT" else "🔴"
        text = (f"{mark} {verb} {f['symbol']}  {f['shares']:,.0f} @ {f['avg']:.2f}"
                f"   ({_et_stamp(f['time'])} ET)")
        if f.get("pnl") is not None:
            text += f"\nP&L {f['pnl']:+,.0f} USD"
        if not self.armed:
            return
        # A blocking POST would stall the refresh loop, so it goes out on its
        # own thread; delivery is best-effort and never worth a frame for.
        def deliver() -> None:
            from .notify import send_telegram
            if send_telegram(self.cfg, text):
                self.sent += 1
            else:
                self.failed += 1
        threading.Thread(target=deliver, daemon=True).start()


# ---------- session phase ----------

def phase(cfg: LiveConfig, now: datetime) -> tuple[str, str, str]:
    """(phase text, style, countdown to the next milestone)."""
    if now.weekday() >= 5:
        return "market closed (weekend)", "dim", ""
    m = now.hour * 60 + now.minute
    marks = [(hhmm_to_min(cfg.scan_time), "scanner runs"),
             (hhmm_to_min(cfg.entry_end), "entries close"),
             (hhmm_to_min(cfg.eod_flat), "EOD flatten"),
             (hhmm_to_min(cfg.shutdown), "shutdown")]
    if m < 9 * 60 + 30:
        text, style = "pre-market", "yellow"
    elif m < marks[0][0]:
        text, style = "waiting for scanner", "yellow"
    elif m < marks[1][0]:
        text, style = "TRADING WINDOW", "bold green"
    elif m < marks[2][0]:
        text, style = "managing positions (no new entries)", "cyan"
    elif m < marks[3][0]:
        text, style = "EOD flatten", "magenta"
    else:
        return "session over", "dim", ""
    nxt = next(((t, lbl) for t, lbl in marks if t > m), None)
    if nxt is None:
        return text, style, ""
    left = nxt[0] * 60 - (m * 60 + now.second)
    if left >= 3600:
        return text, style, f"{nxt[1]} in {left // 3600}h {left % 3600 // 60:02d}m"
    return text, style, f"{nxt[1]} in {left // 60}m {left % 60:02d}s"


# ---------- panels ----------

LOG_STYLES = [("KILL", "bold red"), ("ERROR", "bold red"), ("error", "bold red"),
              ("WARNING", "yellow"), ("setup:", "bold cyan"),
              ("entry filled", "bold green"), ("EOD flatten", "magenta"),
              ("session done", "magenta"), ("live engine up", "green"),
              ("scanner picks", "bold cyan"), ("connected", "green")]


def panel_status(cfg: LiveConfig, now: datetime, feed: "IBFeed | None" = None,
                 alerter: "FillAlerter | None" = None) -> tuple[Panel, int]:
    """Returns the panel and how many rows it needs, so render() can size it."""
    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim", justify="right")
    t.add_column()

    proc = engine_proc()
    if proc is None:
        t.add_row("engine", Text("NOT RUNNING", style="bold red"))
    else:
        up = int(time.time() - proc.info["create_time"])
        t.add_row("engine", Text(f"running  PID {proc.pid}", style="bold green"))
        t.add_row("uptime", f"{up // 3600}h {up % 3600 // 60:02d}m")

    mode = "PAPER" if cfg.port in (4002, 7497) else "LIVE"
    t.add_row("mode", Text(mode, style="green" if mode == "PAPER" else "bold red"))

    flag = query("select value from flags where name='trading_enabled'")
    enabled = (flag[0][0] if flag else "1") == "1"
    t.add_row("trading_enabled", Text("ON" if enabled else "OFF",
                                      style="green" if enabled else "bold red"))

    d = query("select start_equity, end_equity, realized_pnl, n_trades, killed "
              "from daily where date=?", (str(now.date()),))
    # Every figure below is labelled by what it actually measures. Two traps:
    # `daily.start_equity` is rewritten on each engine restart, so it is the
    # session's baseline and not the market open; and the engine never writes
    # the `trades` table at all, so anything derived from it would read zero
    # forever rather than "nothing closed yet".
    if d:
        start, end, _pnl, _n, killed = d[0]
        if start:
            t.add_row("equity @start", f"${start:,.0f}")
        if end:
            t.add_row("equity @close", f"${end:,.0f}")
    else:
        t.add_row("session", Text("no record yet today", style="dim"))
        start = None

    if feed is not None:
        live_eq = feed.equity()
        t.add_row("equity now", f"${live_eq:,.0f}" if _num(live_eq)
                  else Text("unavailable", style="red"))
        day = feed.day_pnl()
        if day is not None:
            pct = f"  ({day / (live_eq - day) * 100:+.2f}%)" \
                if _num(live_eq) and live_eq != day else ""
            t.add_row("day P&L", Text(f"${day:,.0f}{pct}",
                                      style="green" if day >= 0 else "red"))
        elif _num(live_eq) and start:
            chg = (live_eq - start) / start * 100
            t.add_row("since start", Text(f"{chg:+.2f}%",
                                          style="green" if chg >= 0 else "red"))
    else:
        t.add_row("equity now", Text("needs --ib", style="dim"))

    placed = query("select count(*) from orders where substr(ts, 1, 10)=? "
                   "and action='BUY'", (str(now.date()),))
    t.add_row("entry orders", str(placed[0][0] if placed else 0))
    closed = query("select count(*), coalesce(sum(pnl_usd), 0) from trades "
                   "where substr(entry_time, 1, 10)=?", (str(now.date()),))
    n_closed, pnl_closed = closed[0] if closed else (0, 0.0)
    if n_closed:
        t.add_row("closed trades", Text(f"{n_closed}   ${pnl_closed:,.0f}",
                                        style="green" if pnl_closed >= 0 else "red"))
    else:
        t.add_row("closed trades", Text("not recorded by engine", style="yellow"))
    if d and killed:
        t.add_row("kill switch", Text("TRIGGERED", style="bold red"))

    t.add_row("risk/trade", f"{cfg.risk_per_trade_pct}%   max {cfg.max_concurrent} open")
    t.add_row("kill at", f"-{cfg.daily_loss_limit_pct}% day P&L")
    t.add_row("watchlist", f"top {cfg.scanner_top_k} at {cfg.scan_time}")
    if alerter is not None:
        if not alerter.armed:
            t.add_row("fill alerts", Text("telegram not configured", style="dim"))
        else:
            note = f"telegram  {alerter.sent} sent"
            if alerter.failed:
                note += f", {alerter.failed} failed"
            t.add_row("fill alerts", Text(note, style="red" if alerter.failed else "green"))

    # heartbeat: how stale the log is, so idle-by-design is distinguishable
    # from stuck. The engine only logs on events, so age alone is not an alarm.
    try:
        age = int(time.time() - os.path.getmtime(LOG_PATH))
        t.add_row("last log", f"{age // 60}m {age % 60:02d}s ago")
    except OSError:
        pass
    return Panel(t, title="status", border_style="blue", box=box.ROUNDED), t.row_count


def panel_picks(now: datetime, cfg: LiveConfig, feed: "IBFeed | None") -> Panel:
    rows = query("select symbol, score, gap, early_move, early_rv from scanner_log "
                 "where date=? order by score desc", (str(now.date()),))
    title = f"watchlist - scanner picks at {cfg.scan_time}"
    if not rows:
        hint = f"no picks yet - the scanner runs at {cfg.scan_time} ET"
        return Panel(Text(hint, style="dim"), title=title,
                     border_style="cyan", box=box.ROUNDED)

    syms = [r[0] for r in rows]
    if feed is not None:
        feed.watch(syms)

    t = Table(box=box.SIMPLE, expand=True, pad_edge=False)
    # gap and relvol are the scanner's rationale and never change during the
    # day; score and early move stay in the 10:00 log line rather than taking
    # width from "needs", which is the only column that moves bar to bar.
    cols = [("symbol", "left"), ("gap", "right"), ("rv", "right")]
    # Without a broker feed there is no live price and no way to evaluate the
    # entry conditions. Keep the columns and say so, rather than dropping them:
    # a table that silently loses three columns looks complete and is not.
    cols += [("last", "right"), ("day", "right"), ("needs for entry", "left")]
    for c, j in cols:
        # never wrap: a two-line row silently pushes the last symbol out of a
        # content-sized pane, which is how AMLX kept disappearing
        t.add_column(c, justify=j, no_wrap=True, overflow="ellipsis")
    for sym, _score_v, gap, _move, rv in rows:
        cells = [Text(sym, style="bold"), f"{gap:+.1%}", f"{rv:.1f}"]
        if feed is None:
            cells += [Text("-", style="dim"), Text("-", style="dim"),
                      Text("needs --ib", style="yellow")]
        else:
            last, chg = feed.quote(sym)
            cells.append(f"{last:.2f}" if _num(last) else "-")
            cells.append(Text(f"{chg:+.2%}", style="green" if chg >= 0 else "red")
                         if chg == chg else Text("-", style="dim"))
            cells.append(_needs(feed.gates(sym), feed.bar_age(sym)))
        t.add_row(*cells)
    return Panel(t, title=title, border_style="cyan", box=box.ROUNDED)


STALE_BAR_S = 180.0


def _needs(gates, age: float = float("nan")) -> Text:
    """The conditions still blocking an entry, or READY when none are.

    A stale feed outranks the gate list: conditions computed on frozen bars
    describe the past, and reporting them as current is worse than useless.
    """
    if age == age and age > STALE_BAR_S:
        return Text(f"STALE FEED - {age / 60:.0f}m old", style="bold red")
    if gates is None:
        return Text("waiting for bars", style="dim")
    missing = [name for name, ok in gates if not ok]
    if not missing:
        return Text("READY", style="bold green")
    shown = ", ".join(missing[:3]) + (f" +{len(missing) - 3}" if len(missing) > 3 else "")
    return Text(shown, style="yellow" if len(missing) <= 2 else "dim")


def panel_positions(feed: "IBFeed | None") -> Panel:
    if feed is None:
        return Panel(Text("run with --ib to show live positions and prices",
                          style="dim"), title="positions",
                     border_style="green", box=box.ROUNDED)
    items = feed.portfolio()
    if items is None:
        return Panel(Group(Text("UNKNOWN - no broker connection", style="bold red"),
                           Text("positions cannot be confirmed", style="dim"),
                           feed.status),
                     title="positions", border_style="red", box=box.ROUNDED)
    if not items:
        return Panel(Group(Text("flat - no open positions", style="dim"),
                           feed.status),
                     title="positions", border_style="green", box=box.ROUNDED)
    t = Table(box=box.SIMPLE, expand=True, pad_edge=False)
    for c, j in [("symbol", "left"), ("qty", "right"), ("avg", "right"),
                 ("last", "right"), ("value", "right"), ("unreal P&L", "right")]:
        t.add_column(c, justify=j)
    for p in items:
        pnl = p.unrealizedPNL or 0.0
        t.add_row(Text(p.contract.symbol, style="bold"), f"{p.position:,.0f}",
                  f"{p.averageCost:.2f}", f"{p.marketPrice:.2f}",
                  f"${p.marketValue:,.0f}",
                  Text(f"${pnl:,.0f}", style="green" if pnl >= 0 else "red"))
    return Panel(Group(t, feed.status), title="positions",
                 border_style="green", box=box.ROUNDED)


def _et_stamp(ts) -> str:
    """dd/mm HH:MM in ET. Order rows are stored in UTC, so convert first.

    The date matters because the orders table and the broker's execution list
    both carry more than one session: without it, a fill from two days ago
    reads as if it happened this morning.
    """
    try:
        t = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
        if t.tzinfo is None:
            t = t.replace(tzinfo=ZoneInfo("UTC"))
        return t.astimezone(ZoneInfo("US/Eastern")).strftime("%d/%m %H:%M")
    except (ValueError, TypeError):
        return str(ts)[:16].replace("T", " ")


def panel_activity(rows: int = 8, feed: "IBFeed | None" = None) -> Panel:
    """Broker fills when connected, otherwise the orders the engine logged.

    Fills are what actually happened, and they are the only place an exit shows
    up at all -- the engine records the entry order and nothing else. A table
    here gets crushed to stubs in a narrow pane, so each record is one line.
    """
    body = Text(no_wrap=True, overflow="ellipsis")
    fills = feed.fills() if feed is not None else []
    if fills:
        for f in fills[:rows]:
            body.append(_et_stamp(f["time"]) + " ", style="dim")
            body.append(f"{f['symbol']} ", style="bold")
            body.append("BUY  " if f["side"] == "BOT" else "SELL ",
                        style="green" if f["side"] == "BOT" else "red")
            body.append(f"{f['shares']:,.0f} @{f['avg']:.2f}")
            if f.get("pnl") is not None:
                body.append(f"  {f['pnl']:+,.0f}",
                            style="bold green" if f["pnl"] >= 0 else "bold red")
            body.append("\n")
        return Panel(body, title="fills (broker)", border_style="magenta",
                     box=box.ROUNDED)

    orders = query("select ts, symbol, action, qty, order_type, aux_price, status "
                   "from orders order by id desc limit ?", (rows,))
    for ts, sym, act, qty, otype, px, status in orders:
        body.append(_et_stamp(ts) + " ", style="dim")
        body.append(f"{sym} ", style="bold")
        body.append(f"{act} ", style="green" if act == "BUY" else "red")
        body.append(f"{qty:,} {otype or ''}")
        body.append(f" @{px:.2f}" if px else "")
        body.append(f"  {status or ''}\n", style="dim")
    if not orders:
        return Panel(Text("no orders or fills yet", style="dim"), title="activity",
                     border_style="magenta", box=box.ROUNDED)
    body.append("\nentries only - exits are not logged", style="yellow")
    return Panel(body, title="orders (engine log)", border_style="magenta",
                 box=box.ROUNDED)


def panel_log(rows: int) -> Panel:
    lines = log_tail(rows)
    if not lines:
        return Panel(Text("log is empty", style="dim"), title="live.log",
                     border_style="white", box=box.ROUNDED)
    # One log line must occupy exactly one display row: wrapped lines push the
    # newest entries out of a short pane, which is the opposite of useful.
    out = Text(no_wrap=True, overflow="ellipsis")
    for raw in lines:
        style = next((s for key, s in LOG_STYLES if key in raw), None)
        ln = safe(raw)
        m = re.match(r"^(?:[\d-]{10} )?([\d:,]{8,12})\s+(.*)$", ln)
        if m:
            out.append(m.group(1)[:8] + "  ", style="dim")
            out.append(m.group(2) + "\n", style=style)
        else:
            out.append(ln + "\n", style=style)
    return Panel(out, title=f"live.log (last {rows})", border_style="white", box=box.ROUNDED)


def render(cfg: LiveConfig, rows: int, feed: "IBFeed | None" = None,
           width: int = 0, alerter: "FillAlerter | None" = None) -> Layout:
    now = now_et()
    ptext, pstyle, countdown = phase(cfg, now)
    head = Table.grid(expand=True)
    head.add_column(justify="left")
    head.add_column(justify="center")
    head.add_column(justify="right")
    head.add_row(Text(f"ET {now:%H:%M:%S}  {now:%a %d %b}", style="bold white"),
                 Text(ptext, style=pstyle),
                 Text(countdown, style="dim"))

    # Size the panes to their contents: a fixed height silently truncates the
    # last watchlist row, which is exactly the row you need when top_k grows.
    n_picks = len(query("select 1 from scanner_log where date=?", (str(now.date()),)))
    held = feed.portfolio() if feed is not None else None
    n_pos = len(held) if held else 0     # None means unknown, not empty
    picks_h = max(n_picks + 5, 6)          # border + title + header + rule
    pos_h = max(n_pos + 6, 6)              # + one line for the feed status
    status_panel, status_rows = panel_status(cfg, now, feed, alerter)
    # the tallest column wins, or the status pane loses its last rows -- which
    # is where the alert state and the heartbeat live. On a narrow terminal the
    # activity pane sits under status and needs counting too.
    status_h = status_rows + 2 + (7 if width and width < 150 else 0)
    mid_h = max(15, picks_h + pos_h, status_h)

    lay = Layout()
    lay.split_column(Layout(Panel(head, border_style="blue", box=box.ROUNDED),
                            name="head", size=3),
                     Layout(name="mid", size=mid_h),
                     Layout(name="bottom", ratio=1))
    status_pane = Layout(status_panel, name="status", ratio=2)
    if width and width < 150:
        status_pane = Layout(name="status", ratio=2)
    lay["mid"].split_row(status_pane, Layout(name="right", ratio=3))
    if width and width < 150:
        lay["status"].split_column(Layout(status_panel),
                                   Layout(panel_activity(4, feed), size=7))
    lay["right"].split_column(Layout(panel_picks(now, cfg, feed), size=picks_h),
                              Layout(panel_positions(feed)))
    # Below ~150 columns the side-by-side split starves both panes, so the log
    # takes the full width and activity moves under the status column.
    if width and width < 150:
        lay["bottom"].update(panel_log(rows))
    else:
        lay["bottom"].split_row(Layout(panel_log(rows), ratio=3),
                                Layout(panel_activity(feed=feed), ratio=1))
    return lay


def main() -> None:
    # Windows consoles still default to a legacy codepage; UTF-8 keeps box
    # drawing and any stray emoji from raising mid-render.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
    cfg = load_config()
    console = Console()
    feed = IBFeed(cfg) if "--ib" in sys.argv else None
    alerter = FillAlerter(cfg) if feed is not None else None

    fixed = int(sys.argv[sys.argv.index("--rows") + 1]) if "--rows" in sys.argv else 0

    def log_rows() -> int:
        """Fill the log pane exactly; render() sizes the panes above it."""
        return fixed or max(4, console.size.height - 26)

    try:
        if "--once" in sys.argv:
            if feed is not None:      # give quotes a moment to arrive
                render(cfg, log_rows(), feed, console.size.width, alerter)
                feed.pump(3)
            console.print(render(cfg, log_rows(), feed, console.size.width, alerter))
            return
        with Live(render(cfg, log_rows(), feed, console.size.width, alerter), console=console,
                  refresh_per_second=4, screen=True) as live:
            failures = 0
            while True:
                if feed is not None:
                    feed.pump(2)
                else:
                    time.sleep(2)
                if alerter is not None and feed is not None:
                    alerter.poll(feed.fills())
                try:
                    live.update(render(cfg, log_rows(), feed, console.size.width, alerter))
                    failures = 0
                except Exception:  # noqa: BLE001
                    # One bad frame keeps the previous one on screen; a run of
                    # them is a real fault worth surfacing rather than hiding.
                    failures += 1
                    if failures >= 5:
                        raise
                    if feed is not None:
                        feed.drop("render failed - reconnecting")
    except KeyboardInterrupt:
        console.print("[dim]monitor stopped - the engine was not touched[/dim]")
    finally:
        if feed is not None:
            feed.close()


if __name__ == "__main__":
    main()
