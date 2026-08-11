"""Yahoo Finance intraday data fetching with on-disk caching.

Yahoo intraday limits: 1m bars only for the last ~30 days (max 8 days per
request), 5m/15m/30m for the last ~60 days. Regular trading hours only.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import pandas as pd
import requests
import yfinance as yf

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_cache")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

INTERVALS = {"1m": 1, "5m": 5, "15m": 15, "30m": 30}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": _UA})
    return s


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.title)[["Open", "High", "Low", "Close", "Volume"]]
    return df


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna()
    df = df[df["Volume"] > 0]
    # regular session only (times are exchange-local, US/Eastern)
    df = df.between_time("09:30", "15:59")
    df = df[~df.index.duplicated(keep="first")]
    return df.sort_index()


def fetch_intraday(symbol: str, interval: str, days: int, session: requests.Session | None = None,
                   use_cache: bool = True) -> pd.DataFrame:
    """Fetch intraday OHLCV for `symbol`. Returns tz-aware (US/Eastern) DataFrame."""
    assert interval in INTERVALS, f"bad interval {interval}"
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"{symbol}_{interval}_{days}d.parquet")
    if use_cache and os.path.exists(cache_file):
        age_h = (time.time() - os.path.getmtime(cache_file)) / 3600
        if age_h < 24:
            return pd.read_parquet(cache_file)

    session = session or _session()
    if interval == "1m":
        # chunked fetch: <=8 days per request, up to ~29 days back
        days = min(days, 29)
        frames = []
        end = datetime.utcnow()
        start_limit = end - timedelta(days=days)
        chunk_end = end
        while chunk_end > start_limit:
            chunk_start = max(chunk_end - timedelta(days=7), start_limit)
            try:
                df = yf.download(symbol, start=chunk_start.strftime("%Y-%m-%d"),
                                 end=(chunk_end + timedelta(days=1)).strftime("%Y-%m-%d"),
                                 interval="1m", progress=False, auto_adjust=True,
                                 session=session, prepost=False)
                if len(df):
                    frames.append(_flatten(df))
            except Exception as e:  # noqa: BLE001 - keep going on chunk failure
                print(f"  warn: {symbol} 1m chunk {chunk_start:%Y-%m-%d} failed: {e}")
            chunk_end = chunk_start
            time.sleep(0.4)
        out = pd.concat(frames).pipe(_clean) if frames else pd.DataFrame()
    else:
        days = min(days, 59)
        df = yf.download(symbol, period=f"{days}d", interval=interval, progress=False,
                         auto_adjust=True, session=session, prepost=False)
        out = _clean(_flatten(df)) if len(df) else pd.DataFrame()

    if len(out):
        out.to_parquet(cache_file)
    return out


def fetch_universe(symbols: list[str], intervals: list[str], days_1m: int = 29,
                   days_other: int = 59) -> dict[tuple[str, str], pd.DataFrame]:
    """Fetch all (symbol, interval) pairs. Returns dict keyed by (symbol, interval)."""
    session = _session()
    data: dict[tuple[str, str], pd.DataFrame] = {}
    for sym in symbols:
        for iv in intervals:
            days = days_1m if iv == "1m" else days_other
            df = fetch_intraday(sym, iv, days, session=session)
            if len(df) > 100:
                data[(sym, iv)] = df
                print(f"  {sym} {iv}: {len(df)} bars  {df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d}")
            else:
                print(f"  {sym} {iv}: insufficient data ({len(df)} bars) — skipped")
            time.sleep(0.3)
    return data
