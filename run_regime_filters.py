#!/usr/bin/env python3
"""Regime-filter experiment: does requiring the market / sector to be healthy
at entry time improve the strategy?

Filters tested (all lookahead-free, all evaluated at the signal bar):
  SPY  above its session VWAP
  QQQ  above its session VWAP
  SECT above its session VWAP — the symbol's own sector ETF
       (SMH semis, IBIT crypto-proxies, XLK tech, XLY consumer, XLF fintech,
        XLC communication)
plus AND-combinations. Applied to the production 5m+15m ensemble and to the
1m curated config, with train/validation splits.
"""
from __future__ import annotations

import os

import pandas as pd

from engine.backtest import simulate_symbol, run_portfolio
from engine.data import fetch_universe, fetch_intraday, _session
from engine.indicators import enrich, session_vwap
from engine.metrics import compute_metrics
from engine.optimize import prepare, _split_enriched
from engine.strategy import with_
from run_backtest import UNIVERSE, INTERVALS, RESULTS
from run_final_report import FINAL_CONFIGS
from run_1m_broad import CONFIG_1M

SECTOR_ETF = {
    "NVDA": "SMH", "AMD": "SMH", "MU": "SMH", "AVGO": "SMH", "INTC": "SMH",
    "SMCI": "SMH", "IONQ": "XLK", "MSFT": "XLK", "AAPL": "XLK", "ORCL": "XLK",
    "CRWD": "XLK", "SHOP": "XLK", "PLTR": "XLK", "UBER": "XLK",
    "GOOGL": "XLC", "META": "XLC", "NFLX": "XLC", "ROKU": "XLC", "SNAP": "XLC",
    "TSLA": "XLY", "AMZN": "XLY", "NIO": "XLY", "RIVN": "XLY", "DKNG": "XLY",
    "COIN": "IBIT", "MSTR": "IBIT", "MARA": "IBIT", "RIOT": "IBIT",
    "HOOD": "XLF", "SOFI": "XLF", "AFRM": "XLF",
}
REF_SYMBOLS = ["SPY", "QQQ"] + sorted(set(SECTOR_ETF.values()))


def build_flags(ref_data: dict) -> dict:
    """(ref_symbol, interval) -> pd.Series of above-VWAP bools."""
    flags = {}
    for (sym, iv), df in ref_data.items():
        E = enrich(df)
        flags[(sym, iv)] = pd.Series(E["close"] > E["vwap"], index=E["index"])
    return flags


def inject(enriched: dict, flags: dict, mode: str) -> None:
    """Set E['mkt_ok'] per symbol according to the variant."""
    for (sym, iv), E in enriched.items():
        ok = pd.Series(True, index=E["index"])
        def flag_of(ref):
            f = flags.get((ref, iv))
            if f is None:
                return None
            return f.reindex(E["index"], method="ffill").fillna(False)
        parts = []
        if "spy" in mode:
            parts.append(flag_of("SPY"))
        if "qqq" in mode:
            parts.append(flag_of("QQQ"))
        if "sect" in mode:
            etf = SECTOR_ETF.get(sym)
            if etf:
                parts.append(flag_of(etf))
        for p in parts:
            if p is not None:
                ok &= p
        E["mkt_ok"] = ok.to_numpy(bool)


def run_ensemble(enriched, configs, part=None):
    trades, seen = [], set()
    for p in configs:
        for (sym, iv), E in enriched.items():
            if iv != p.timeframe or sym in REF_SYMBOLS:
                continue
            if part is not None:
                cut = int(E["day"].max() * 0.7) + 1
                E = _split_enriched(E, cut, part)
                if len(E["open"]) < 100:
                    continue
            for t in simulate_symbol(sym, E, p):
                key = (t.symbol, str(t.entry_time))
                if key not in seen:
                    seen.add(key)
                    trades.append(t)
    curve, tdf = run_portfolio(trades, max_concurrent=6, sizing_mode="risk",
                               risk_per_trade_pct=1.5, pos_leverage_cap=2.5)
    return compute_metrics(curve, tdf)


def main() -> None:
    print("=== Loading universe + reference ETF data ===")
    data = fetch_universe(UNIVERSE, INTERVALS)
    ref_data = {}
    session = _session()
    for ref in REF_SYMBOLS:
        for iv in ("1m", "5m", "15m"):
            df = fetch_intraday(ref, iv, 29 if iv == "1m" else 59, session=session)
            if len(df) > 100:
                ref_data[(ref, iv)] = df
    flags = build_flags(ref_data)
    enriched = prepare(data)

    variants = ["none", "spy", "qqq", "sect", "spy+sect", "qqq+sect"]
    rows = []
    for engine_name, configs in [
        ("5m+15m prod", [with_(p, market_filter=True) for p in FINAL_CONFIGS]),
        ("1m curated", [with_(CONFIG_1M, market_filter=True),
                        with_(CONFIG_1M, market_filter=True, rsi_filter=True)]),
    ]:
        base_cfgs = [with_(p, market_filter=False) for p in configs]
        for mode in variants:
            cfgs = base_cfgs if mode == "none" else configs
            if mode != "none":
                inject(enriched, flags, mode)
            full = run_ensemble(enriched, cfgs)
            tr = run_ensemble(enriched, cfgs, part="train")
            va = run_ensemble(enriched, cfgs, part="validation")
            rows.append({"engine": engine_name, "filter": mode,
                         "full_ret": full["total_return_pct"], "full_pf": full["profit_factor"],
                         "full_n": full["n_trades"], "full_dd": full["max_dd_pct"],
                         "train_ret": tr["total_return_pct"], "train_pf": tr["profit_factor"],
                         "val_ret": va["total_return_pct"], "val_pf": va["profit_factor"],
                         "val_n": va["n_trades"]})
            r = rows[-1]
            print(f"  {engine_name:>12} | {mode:>8} | full {r['full_ret']:+7.2f}% "
                  f"pf {r['full_pf']:4.2f} n={r['full_n']:3} dd {r['full_dd']:+6.2f}% | "
                  f"train {r['train_ret']:+6.2f}% | val {r['val_ret']:+6.2f}% "
                  f"pf {r['val_pf']:4.2f} n={r['val_n']}")
    pd.DataFrame(rows).to_csv(os.path.join(RESULTS, "regime_filters.csv"), index=False)
    print("saved results/regime_filters.csv")


if __name__ == "__main__":
    main()
