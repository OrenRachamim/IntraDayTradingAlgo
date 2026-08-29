"""End-to-end pipeline: load data -> screens -> VCP setups -> backtest -> summary.

A DataCache keeps the expensive artifacts (symbol arrays, RS ranks, masks,
setups) in memory keyed by the sub-config that produced them, so iteration
sweeps only recompute what actually changed.
"""
from __future__ import annotations

import dataclasses
import json
import sys
import time
import warnings

import numpy as np
import pandas as pd

from .backtester import Backtester, BacktestResult
from .config import Config
from .data import (DATA_DIR, Market, SymbolData, eligible_symbols, load_benchmark,
                   load_calendar, load_symbol)
from .metrics import summarize
from .trend_template import liquidity_mask, rs_percentiles, trend_template_mask
from .vcp_detector import detect_setups


def _key(*parts) -> str:
    return json.dumps([dataclasses.asdict(p) if dataclasses.is_dataclass(p) else p
                       for p in parts], sort_keys=True)


class DataCache:
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self._store: dict[str, object] = {}

    def log(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr, flush=True)

    def get(self, key: str, builder):
        if key not in self._store:
            t0 = time.time()
            self._store[key] = builder()
            self.log(f"  [cache build {time.time()-t0:6.1f}s] {key[:100]}")
        return self._store[key]


def run_pipeline(cfg: Config, cache: DataCache | None = None) -> tuple[BacktestResult, dict]:
    cache = cache or DataCache()
    bt = cfg.backtest

    cal_key = _key("calendar", bt.start, bt.end)
    calendar: pd.DatetimeIndex = cache.get(
        cal_key, lambda: load_calendar(bt.start, bt.end))
    start_idx = int(np.searchsorted(calendar.values, np.datetime64(bt.start)))

    market: Market = cache.get(
        _key("market", bt.start, bt.end, cfg.entry.market_filter_ma),
        lambda: load_benchmark(calendar, cfg.entry.market_filter_ma))

    def build_data() -> dict[str, SymbolData]:
        syms = eligible_symbols(calendar, cfg.universe.min_history_days)
        out: dict[str, SymbolData] = {}
        for s in syms:
            sd = load_symbol(s, calendar)
            if sd is not None and (sd.last_idx - sd.first_idx) >= cfg.universe.min_history_days:
                out[s] = sd
        return out

    data: dict[str, SymbolData] = cache.get(
        _key("data", bt.start, bt.end, cfg.universe.min_history_days), build_data)
    symbols = sorted(data.keys())

    rs_pct: dict[str, np.ndarray] = cache.get(
        _key("rs", bt.start, bt.end, cfg.universe.min_history_days),
        lambda: rs_percentiles(symbols, data))

    tt_masks: dict[str, np.ndarray] = cache.get(
        _key("tt", bt.start, bt.end, cfg.universe.min_history_days, cfg.tt),
        lambda: {s: trend_template_mask(data[s], rs_pct[s], cfg) for s in symbols})

    liq_masks: dict[str, np.ndarray] = cache.get(
        _key("liq", bt.start, bt.end, cfg.universe),
        lambda: {s: liquidity_mask(data[s], cfg) for s in symbols})

    def build_setups():
        out = {}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            for s in symbols:
                st = detect_setups(data[s], cfg)
                if st:
                    out[s] = st
        return out

    setups = cache.get(
        _key("setups", bt.start, bt.end, cfg.universe.min_history_days,
             cfg.vcp, cfg.entry.breakout_buffer), build_setups)

    engine = Backtester(cfg, calendar, data, setups, tt_masks, liq_masks,
                        rs_pct, market, start_idx)
    result = engine.run()

    spy_series = pd.Series(market.close, index=calendar)
    summary = summarize(result.equity, result.trades, result.daily_exposure, spy_series)
    summary["n_setups"] = result.n_setups
    summary["n_triggered"] = result.n_triggered
    summary["n_symbols"] = len(symbols)
    summary["config_name"] = cfg.name
    return result, summary
