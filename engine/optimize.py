"""Staged grid-search optimizer for the Micro Pullback strategy."""
from __future__ import annotations

import itertools
from dataclasses import asdict

import numpy as np
import pandas as pd

from .backtest import simulate_symbol, run_portfolio
from .indicators import enrich
from .metrics import compute_metrics
from .strategy import Params, with_


def prepare(data: dict, spy_data: dict | None = None) -> dict:
    """enrich() every (symbol, interval) frame once.

    If spy_data (interval -> SPY DataFrame) is given, attach an aligned
    'mkt_ok' flag (SPY above its session VWAP) to every frame.
    """
    enriched = {key: enrich(df) for key, df in data.items()}
    if spy_data:
        for iv, sdf in spy_data.items():
            SE = enrich(sdf)
            flag = pd.Series(SE["close"] > SE["vwap"], index=SE["index"])
            for (sym, kiv), E in enriched.items():
                if kiv != iv:
                    continue
                aligned = flag.reindex(E["index"], method="ffill").fillna(False)
                E["mkt_ok"] = aligned.to_numpy(bool)
    return enriched


def _split_enriched(E: dict, day_cut: int, part: str) -> dict:
    """Slice an enriched dict by day code for train/validation splits."""
    mask = E["day"] < day_cut if part == "train" else E["day"] >= day_cut
    out = {}
    for k, v in E.items():
        if k == "index":
            out[k] = v[mask]
        elif isinstance(v, np.ndarray):
            out[k] = v[mask]
        else:
            out[k] = v
    return out


def evaluate(enriched: dict, p: Params, symbols_filter: list[str] | None = None,
             part: str | None = None, split_frac: float = 0.7) -> dict:
    """Run one param set across the universe at p.timeframe; return metrics row."""
    all_trades = []
    for (sym, iv), E in enriched.items():
        if iv != p.timeframe:
            continue
        if symbols_filter and sym not in symbols_filter:
            continue
        if part:
            cut = int(E["day"].max() * split_frac) + 1
            E = _split_enriched(E, cut, part)
            if len(E["open"]) < 100:
                continue
        all_trades.extend(simulate_symbol(sym, E, p))
    curve, tdf = run_portfolio(all_trades, max_concurrent=p.max_concurrent,
                               sizing_mode=p.sizing_mode,
                               risk_per_trade_pct=p.risk_per_trade_pct,
                               pos_leverage_cap=p.pos_leverage_cap)
    m = compute_metrics(curve, tdf)
    m.update({k: v for k, v in asdict(p).items()})
    m["_curve"] = curve
    m["_trades"] = tdf
    return m


def grid(base: Params, **axes) -> list[Params]:
    """Cartesian product of keyword axes applied over `base`."""
    keys = list(axes)
    out = []
    for combo in itertools.product(*[axes[k] for k in keys]):
        out.append(with_(base, **dict(zip(keys, combo))))
    return out


def run_grid(enriched: dict, params_list: list[Params], tag: str, top_n: int = 10,
             part: str | None = None) -> pd.DataFrame:
    rows = []
    for k, p in enumerate(params_list):
        m = evaluate(enriched, p, part=part)
        m.pop("_curve"), m.pop("_trades")
        rows.append(m)
        if (k + 1) % 25 == 0:
            print(f"    [{tag}] {k + 1}/{len(params_list)} evaluated")
    df = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
    print(f"  [{tag}] done: {len(df)} configs, best score={df['score'].iloc[0]:.2f} "
          f"ret={df['total_return_pct'].iloc[0]:.2f}% pf={df['profit_factor'].iloc[0]:.2f} "
          f"trades={df['n_trades'].iloc[0]}")
    return df
