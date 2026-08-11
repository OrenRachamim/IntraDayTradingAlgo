"""Vectorized technical indicators computed once per (symbol, timeframe)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["High"] - df["Low"]
    hc = (df["High"] - df["Close"].shift()).abs()
    lc = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP reset at each session start."""
    day = df.index.normalize()
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = (tp * df["Volume"]).groupby(day).cumsum()
    vv = df["Volume"].groupby(day).cumsum()
    return pv / vv.replace(0, np.nan)


def relative_volume(df: pd.DataFrame, window: int = 20) -> pd.Series:
    avg = df["Volume"].rolling(window, min_periods=5).mean()
    return (df["Volume"] / avg.replace(0, np.nan)).fillna(1.0)


def lower_high_runs(high: np.ndarray) -> np.ndarray:
    """L[i] = number of consecutive bars ending at i with high[j] < high[j-1]."""
    n = len(high)
    runs = np.zeros(n, dtype=np.int32)
    for i in range(1, n):
        if high[i] < high[i - 1]:
            runs[i] = runs[i - 1] + 1
    return runs


def pullback_runs(open_: np.ndarray, close: np.ndarray, high: np.ndarray) -> np.ndarray:
    """Looser pullback: consecutive bars that are red OR make a lower high."""
    n = len(high)
    runs = np.zeros(n, dtype=np.int32)
    for i in range(1, n):
        if close[i] < open_[i] or high[i] < high[i - 1]:
            runs[i] = runs[i - 1] + 1
    return runs


def session_cummax(high: np.ndarray, day: np.ndarray) -> np.ndarray:
    """Running high-of-day."""
    out = np.empty_like(high)
    cur = -np.inf
    prev_day = -1
    for i in range(len(high)):
        if day[i] != prev_day:
            cur = high[i]
            prev_day = day[i]
        else:
            cur = max(cur, high[i])
        out[i] = cur
    return out


def session_open(open_: np.ndarray, day: np.ndarray) -> np.ndarray:
    """First open of each session, broadcast across the day."""
    out = np.empty_like(open_)
    cur = open_[0]
    prev_day = -1
    for i in range(len(open_)):
        if day[i] != prev_day:
            cur = open_[i]
            prev_day = day[i]
        out[i] = cur
    return out


def enrich(df: pd.DataFrame, ema_fast: int = 9, ema_slow: int = 20,
           rsi_period: int = 14, atr_period: int = 14, relvol_window: int = 20) -> dict:
    """Precompute all indicator arrays for one (symbol, timeframe) frame.

    Returns a dict of numpy arrays aligned to df rows, used by the strategy scanner.
    """
    close = df["Close"]
    _, _, hist = macd(close)
    idx = df.index
    day_codes = pd.factorize(idx.normalize())[0]
    minutes = idx.hour * 60 + idx.minute
    o_arr = df["Open"].to_numpy(float)
    h_arr = df["High"].to_numpy(float)
    c_arr = close.to_numpy(float)
    day_arr = np.asarray(day_codes)
    return {
        "hod": session_cummax(h_arr, day_arr),
        "day_open": session_open(o_arr, day_arr),
        "pb_runs": pullback_runs(o_arr, c_arr, h_arr),
        "index": idx,
        "open": df["Open"].to_numpy(float),
        "high": df["High"].to_numpy(float),
        "low": df["Low"].to_numpy(float),
        "close": close.to_numpy(float),
        "volume": df["Volume"].to_numpy(float),
        "ema_fast": ema(close, ema_fast).to_numpy(float),
        "ema_slow": ema(close, ema_slow).to_numpy(float),
        "vwap": session_vwap(df).to_numpy(float),
        "rsi": rsi(close, rsi_period).to_numpy(float),
        "macd_hist": hist.to_numpy(float),
        "atr": atr(df, atr_period).to_numpy(float),
        "relvol": relative_volume(df, relvol_window).to_numpy(float),
        "lh_runs": lower_high_runs(df["High"].to_numpy(float)),
        "day": day_codes,
        "minute": np.asarray(minutes, dtype=np.int32),
    }
