"""Data loading: per-symbol parquet files -> calendar-aligned numpy arrays.

The raw dataset (paperswithbacktest/Stocks-Daily-Price) is repartitioned by
scripts/prepare_data.py into data/stocks/{SYMBOL}.parquet with columns
date/open/high/low/close/volume/adj_close. Signals and fills use
split/dividend-adjusted OHLC (raw OHLC scaled by adj_close/close);
liquidity uses unadjusted close * volume.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass
class SymbolData:
    symbol: str
    # calendar-aligned float32 arrays (NaN where the symbol didn't trade)
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    dollar_volume: np.ndarray
    first_idx: int   # first calendar index with data
    last_idx: int    # last calendar index with data


class Market:
    """Benchmark index data + market regime filter."""

    def __init__(self, calendar: pd.DatetimeIndex, spy: pd.DataFrame, filter_ma: int = 200):
        spy = spy.set_index("date").reindex(calendar)
        self.close = spy["adj_close"].to_numpy(dtype=np.float64)
        sma = spy["adj_close"].rolling(filter_ma, min_periods=filter_ma).mean().to_numpy()
        with np.errstate(invalid="ignore"):
            self.regime_ok = self.close > sma
        self.regime_ok = np.where(np.isnan(self.close) | np.isnan(sma), False, self.regime_ok)


def load_calendar(start: str, end: str, warmup_days: int = 420,
                  data_dir: Path = DATA_DIR) -> pd.DatetimeIndex:
    """Trading calendar = SPY dates from (start - warmup) through end."""
    spy = pd.read_parquet(data_dir / "benchmark" / "SPY.parquet")
    lo = pd.Timestamp(start) - pd.Timedelta(days=warmup_days)
    dates = spy["date"][(spy["date"] >= lo) & (spy["date"] <= pd.Timestamp(end))]
    return pd.DatetimeIndex(dates.reset_index(drop=True))


def load_benchmark(calendar: pd.DatetimeIndex, filter_ma: int = 200,
                   data_dir: Path = DATA_DIR) -> Market:
    spy = pd.read_parquet(data_dir / "benchmark" / "SPY.parquet")
    return Market(calendar, spy, filter_ma)


def eligible_symbols(calendar: pd.DatetimeIndex, min_history_days: int,
                     data_dir: Path = DATA_DIR) -> list[str]:
    """Symbols whose history overlaps the calendar window meaningfully."""
    idx = pd.read_csv(data_dir / "symbols_index.csv", parse_dates=["first_date", "last_date"])
    cal_start, cal_end = calendar[0], calendar[-1]
    ok = idx[(idx["last_date"] >= cal_start) &
             (idx["first_date"] <= cal_end) &
             (idx["rows"] >= min_history_days)]
    return sorted(ok["symbol"].tolist())


def load_symbol(symbol: str, calendar: pd.DatetimeIndex,
                data_dir: Path = DATA_DIR) -> SymbolData | None:
    fn = data_dir / "stocks" / f"{symbol.replace('/', '_')}.parquet"
    df = pd.read_parquet(fn)
    df = df.set_index("date").reindex(calendar)
    def col(name: str) -> np.ndarray:
        x = df[name].to_numpy(dtype=np.float64)
        return np.where(x > 0, x, np.nan)   # zero/negative prices are bad ticks

    close_raw = col("close")
    adj = col("adj_close")
    with np.errstate(invalid="ignore", divide="ignore"):
        factor = adj / close_raw
    valid = ~np.isnan(close_raw) & ~np.isnan(factor)
    if valid.sum() == 0:
        return None
    nz = np.flatnonzero(valid)
    return SymbolData(
        symbol=symbol,
        open=(col("open") * factor).astype(np.float32),
        high=(col("high") * factor).astype(np.float32),
        low=(col("low") * factor).astype(np.float32),
        close=np.where(valid, adj, np.nan).astype(np.float32),
        volume=df["volume"].to_numpy(dtype=np.float64).astype(np.float32),
        dollar_volume=(close_raw * df["volume"].to_numpy(dtype=np.float64)).astype(np.float32),
        first_idx=int(nz[0]),
        last_idx=int(nz[-1]),
    )
