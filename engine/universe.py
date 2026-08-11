"""Broad US-equity universe construction with liquidity (volume) filtering.

Membership: S&P 500 + Nasdaq-100 (Wikipedia) + a supplemental list of liquid
high-beta mid caps. Filter: average daily dollar volume and minimum price over
the recent window, computed from batch daily data. The filter is a pure
liquidity criterion (not performance-based), so selection bias is minimal.
"""
from __future__ import annotations

import io
import os
import time

import pandas as pd
import requests
import yfinance as yf

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_cache")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# liquid high-beta names outside the two big indexes
EXTRA = ["MSTR", "COIN", "HOOD", "SOFI", "RIVN", "MARA", "RIOT", "AFRM", "IONQ",
         "RKLB", "ASTS", "OKLO", "CVNA", "UPST", "RDDT", "TEM", "APP", "NET",
         "SNAP", "GME", "AMC", "CLSK", "NIO", "LCID", "PLUG", "DKNG", "ROKU",
         "SQ", "PYPL", "SHOP", "SNOW", "ARM", "SMCI", "ENPH", "VRT"]

FALLBACK = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO",
            "AMD", "NFLX", "MU", "INTC", "PLTR", "UBER", "CRWD", "ORCL"]


def _wiki_symbols(session: requests.Session) -> list[str]:
    syms: list[str] = []
    try:
        r = session.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", timeout=30)
        for t in pd.read_html(io.StringIO(r.text)):
            if "Symbol" in t.columns:
                syms += t["Symbol"].astype(str).tolist()
                break
    except Exception as e:  # noqa: BLE001
        print(f"  warn: S&P500 list fetch failed: {e}")
    try:
        r = session.get("https://en.wikipedia.org/wiki/Nasdaq-100", timeout=30)
        for t in pd.read_html(io.StringIO(r.text)):
            for col in ("Ticker", "Symbol"):
                if col in t.columns and len(t) > 50:
                    syms += t[col].astype(str).tolist()
                    break
    except Exception as e:  # noqa: BLE001
        print(f"  warn: Nasdaq-100 list fetch failed: {e}")
    return syms


def build_universe(min_dollar_vol_m: float = 150.0, min_price: float = 5.0,
                   top_n: int = 150, use_cache: bool = True) -> list[str]:
    """Return up to top_n symbols sorted by avg daily dollar volume (desc)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"universe_{min_dollar_vol_m:.0f}m_{top_n}.csv")
    if use_cache and os.path.exists(cache) and (time.time() - os.path.getmtime(cache)) / 3600 < 24:
        return pd.read_csv(cache)["symbol"].tolist()

    session = requests.Session()
    session.headers.update({"User-Agent": _UA})
    raw = _wiki_symbols(session) + EXTRA
    if len(raw) < 50:
        raw = FALLBACK + EXTRA
    # Yahoo uses '-' for share classes (BRK.B -> BRK-B)
    cands = sorted({s.strip().replace(".", "-") for s in raw if s and s.strip().isascii()})
    print(f"  candidate membership: {len(cands)} symbols")

    rows = []
    for k in range(0, len(cands), 100):
        batch = cands[k:k + 100]
        try:
            df = yf.download(batch, period="45d", interval="1d", progress=False,
                             auto_adjust=True, session=session, group_by="ticker",
                             threads=False)
        except Exception as e:  # noqa: BLE001
            print(f"  warn: daily batch {k} failed: {e}")
            continue
        for s in batch:
            try:
                sub = df[s].dropna()
                if len(sub) < 15:
                    continue
                px = float(sub["Close"].iloc[-1])
                dv = float((sub["Close"] * sub["Volume"]).mean()) / 1e6
                rows.append({"symbol": s, "price": px, "dollar_vol_m": dv})
            except Exception:  # noqa: BLE001
                continue
        time.sleep(0.5)

    uni = pd.DataFrame(rows)
    uni = uni[(uni["dollar_vol_m"] >= min_dollar_vol_m) & (uni["price"] >= min_price)]
    uni = uni.sort_values("dollar_vol_m", ascending=False).head(top_n)
    uni.to_csv(cache, index=False)
    print(f"  universe after volume filter (>= ${min_dollar_vol_m:.0f}M/day, "
          f">= ${min_price:.0f}): {len(uni)} symbols")
    return uni["symbol"].tolist()
