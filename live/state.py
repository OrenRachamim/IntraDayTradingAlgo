"""SQLite persistence: live trades, orders, daily PnL, and control flags.

The 'trading_enabled' flag is the contract between the maintenance job and the
live engine: maintenance may flip it off (with a reason), and the engine
refuses to open new positions while it is off.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from .config import STATE_DIR

DB_PATH = os.path.join(STATE_DIR, "live.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT, qty INTEGER, entry_time TEXT, exit_time TEXT,
    entry_px REAL, exit_px REAL, stop_px REAL, target_px REAL,
    pnl_usd REAL, ret_pct REAL, reason TEXT, mode TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, symbol TEXT, action TEXT, qty INTEGER, order_type TEXT,
    aux_price REAL, ib_order_id INTEGER, status TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS daily (
    date TEXT PRIMARY KEY, start_equity REAL, end_equity REAL,
    realized_pnl REAL, n_trades INTEGER, killed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS flags (
    name TEXT PRIMARY KEY, value TEXT, updated TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS scanner_log (
    date TEXT, symbol TEXT, score REAL, gap REAL, early_move REAL, early_rv REAL
);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(STATE_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def set_flag(name: str, value: str, reason: str = "") -> None:
    with connect() as con:
        con.execute("INSERT INTO flags(name, value, updated, reason) VALUES(?,?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET value=excluded.value, "
                    "updated=excluded.updated, reason=excluded.reason",
                    (name, value, _now(), reason))


def get_flag(name: str, default: str = "") -> str:
    with connect() as con:
        row = con.execute("SELECT value FROM flags WHERE name=?", (name,)).fetchone()
    return row[0] if row else default


def trading_enabled() -> bool:
    return get_flag("trading_enabled", "1") == "1"


def log_order(symbol: str, action: str, qty: int, order_type: str, aux_price: float,
              ib_order_id: int, status: str, note: str = "") -> None:
    with connect() as con:
        con.execute("INSERT INTO orders(ts,symbol,action,qty,order_type,aux_price,"
                    "ib_order_id,status,note) VALUES(?,?,?,?,?,?,?,?,?)",
                    (_now(), symbol, action, qty, order_type, aux_price,
                     ib_order_id, status, note))


def log_trade(**kw) -> None:
    cols = ("symbol", "qty", "entry_time", "exit_time", "entry_px", "exit_px",
            "stop_px", "target_px", "pnl_usd", "ret_pct", "reason", "mode")
    with connect() as con:
        con.execute(f"INSERT INTO trades({','.join(cols)}) VALUES({','.join('?'*len(cols))})",
                    tuple(kw.get(c) for c in cols))


def log_scanner(date: str, picks: list[dict]) -> None:
    with connect() as con:
        con.execute("DELETE FROM scanner_log WHERE date=?", (date,))
        for p in picks:
            con.execute("INSERT INTO scanner_log VALUES(?,?,?,?,?,?)",
                        (date, p["symbol"], p["score"], p["gap"],
                         p["early_move"], p["early_rv"]))


def upsert_daily(date: str, **kw) -> None:
    with connect() as con:
        con.execute("INSERT INTO daily(date) VALUES(?) ON CONFLICT(date) DO NOTHING", (date,))
        for k, v in kw.items():
            con.execute(f"UPDATE daily SET {k}=? WHERE date=?", (v, date))


def live_trade_stats(last_n: int = 200) -> dict:
    """Aggregate live performance for the maintenance drift check."""
    with connect() as con:
        rows = con.execute("SELECT ret_pct, pnl_usd FROM trades WHERE exit_px IS NOT NULL "
                           "ORDER BY id DESC LIMIT ?", (last_n,)).fetchall()
    if not rows:
        return {"n": 0}
    rets = [r[0] for r in rows if r[0] is not None]
    wins = [r for r in rets if r > 0]
    losses = [-r for r in rets if r <= 0]
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else float("inf")
    return {"n": len(rets), "pf": pf,
            "win_rate": 100 * len(wins) / len(rets) if rets else 0.0,
            "avg_ret": sum(rets) / len(rets) if rets else 0.0,
            "total_pnl_usd": sum(r[1] or 0.0 for r in rows)}
