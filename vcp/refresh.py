"""Refresh symbol data from the Yahoo chart API (for the daily scanner).

The bulk dataset updates monthly; the scanner needs bars through the latest
session. fetch_yahoo_history pulls recent daily bars for one symbol and
refresh_symbol_file merges them into the local per-symbol parquet.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

from .data import DATA_DIR

_UA = {"User-Agent": "Mozilla/5.0"}


def fetch_yahoo_history(symbol: str, years: int = 3) -> pd.DataFrame | None:
    now = int(time.time())
    period1 = now - years * 365 * 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={period1}&period2={now}&interval=1d")
    try:
        req = urllib.request.Request(url, headers=_UA)
        payload = json.load(urllib.request.urlopen(req, timeout=30))
        r = payload["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        adj = r["indicators"].get("adjclose", [{}])[0].get("adjclose", q["close"])
        df = pd.DataFrame({
            "date": pd.to_datetime(r["timestamp"], unit="s", utc=True)
                      .tz_convert("America/New_York").normalize().tz_localize(None),
            "open": q["open"], "high": q["high"], "low": q["low"],
            "close": q["close"], "volume": q["volume"], "adj_close": adj,
        }).dropna(subset=["close"]).drop_duplicates(subset="date")
        return df.sort_values("date").reset_index(drop=True)
    except Exception:
        return None


def refresh_symbol_file(symbol: str, data_dir: Path = DATA_DIR,
                        years: int = 3) -> bool:
    """Merge fresh Yahoo bars into data/stocks/{symbol}.parquet.

    Yahoo's adj_close is re-based on every dividend, so the overlap region is
    replaced wholesale with Yahoo's series (consistent within itself) and only
    history older than the fetched window is kept from the bulk dataset."""
    fresh = fetch_yahoo_history(symbol, years)
    if fresh is None or len(fresh) < 50:
        return False
    fn = data_dir / "stocks" / f"{symbol.replace('/', '_')}.parquet"
    if fn.exists():
        old = pd.read_parquet(fn)
        keep = old[old["date"] < fresh["date"].min()]
        merged = pd.concat([keep, fresh], ignore_index=True)
    else:
        merged = fresh
    merged.to_parquet(fn, index=False)
    return True


def refresh_spy(data_dir: Path = DATA_DIR) -> None:
    fresh = fetch_yahoo_history("SPY", years=40)
    if fresh is None or len(fresh) < 1000:
        raise RuntimeError("SPY refresh from Yahoo failed")
    (data_dir / "benchmark").mkdir(parents=True, exist_ok=True)
    fresh.to_parquet(data_dir / "benchmark" / "SPY.parquet", index=False)
