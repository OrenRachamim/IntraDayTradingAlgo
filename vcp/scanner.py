"""Daily entry scanner: which VCP setups are live and waiting to trigger today.

Evaluates every symbol at its own last bar (data freshness may vary between
the bulk dataset and Yahoo-refreshed candidates) and reports setups that are:
confirmed, still inside their active window, support intact, and not yet
broken out — i.e. actionable buy-stop candidates for the next session.

RS percentiles are forward-filled from the last day with a full cross-section,
so a freshly-refreshed symbol keeps its latest known universe rank.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config
from .data import Market, SymbolData
from .trend_template import trend_template_mask
from .vcp_detector import Setup


@dataclass
class ScanRow:
    symbol: str
    asof: pd.Timestamp
    close: float
    pivot: float
    trigger: float
    dist_to_trigger_pct: float
    stop: float
    stop_pct: float
    shares: int
    position_value: float
    n_contractions: int
    final_depth_pct: float
    vdu_ratio: float
    days_active_left: int
    rs_pct: float
    trend_template: bool
    liquidity_ok: bool
    market_regime_on: bool
    staleness_days: int


def _ffill(a: np.ndarray) -> np.ndarray:
    s = pd.Series(a, dtype="float64")
    return s.ffill().to_numpy()


def plan_position(cfg: Config, trigger: float, support_low: float,
                  equity: float) -> tuple[float, int]:
    """Initial stop and share count for a fill at the trigger, per the config."""
    fill = trigger
    exec_price = fill * (1 + (cfg.costs.slippage_bps + cfg.costs.commission_bps) / 1e4)
    stop = fill * (1 - cfg.risk.stop_pct)
    if cfg.risk.stop_use_contraction_low and support_low > stop:
        stop = support_low
    stop = max(stop, fill * (1 - cfg.risk.stop_max_pct))
    risk_ps = exec_price - stop
    if risk_ps <= 0:
        return stop, 0
    shares = int(min(equity * cfg.risk.risk_per_trade / risk_ps,
                     equity * cfg.risk.max_weight / exec_price))
    return stop, max(shares, 0)


def scan_symbol(sd: SymbolData, setups: list[Setup], rs_ff: np.ndarray,
                liq: np.ndarray, market: Market, cfg: Config, asof_idx: int,
                equity: float, max_staleness_days: int) -> ScanRow | None:
    t = sd.last_idx
    if t < asof_idx - max_staleness_days or math.isnan(float(sd.close[t])):
        return None
    live: Setup | None = None
    for s in setups:                       # newest confirmed setup that is still pending
        c = s.confirm_idx
        if c > t or t - c > cfg.vcp.setup_max_active_days:
            continue
        closes = sd.close[c + 1:t + 1].astype(np.float64)
        highs = sd.high[c + 1:t + 1].astype(np.float64)
        trigger = s.pivot * (1 + cfg.entry.breakout_buffer)
        if len(closes) and np.nanmin(closes) < s.support_low:
            continue                        # support broken -> pattern failed
        if len(highs) and np.nanmax(highs) >= trigger:
            continue                        # already broke out
        live = s
    if live is None:
        return None

    tt = trend_template_mask(sd, rs_ff.astype(np.float32), cfg)
    trigger = live.pivot * (1 + cfg.entry.breakout_buffer)
    close = float(sd.close[t])
    stop, shares = plan_position(cfg, trigger, live.support_low, equity)
    return ScanRow(
        symbol=sd.symbol, asof=pd.NaT, close=round(close, 2),
        pivot=round(live.pivot, 2), trigger=round(trigger, 2),
        dist_to_trigger_pct=round((trigger / close - 1) * 100, 2),
        stop=round(stop, 2), stop_pct=round((1 - stop / trigger) * 100, 2),
        shares=shares, position_value=round(shares * trigger, 0),
        n_contractions=live.n_contractions,
        final_depth_pct=round(live.depths[-1] * 100, 2),
        vdu_ratio=live.vdu_ratio,
        days_active_left=cfg.vcp.setup_max_active_days - (t - live.confirm_idx),
        rs_pct=round(float(rs_ff[t]), 1) if not math.isnan(rs_ff[t]) else float("nan"),
        trend_template=bool(tt[t]),
        liquidity_ok=bool(liq[t]),
        market_regime_on=bool(market.regime_ok[asof_idx]),
        staleness_days=int(asof_idx - t),
    )


def scan(artifacts: dict, cfg: Config, equity: float,
         max_staleness_days: int = 30, require_gates: bool = True) -> pd.DataFrame:
    calendar = artifacts["calendar"]
    market: Market = artifacts["market"]
    asof_idx = len(calendar) - 1
    rows: list[ScanRow] = []
    for sym in artifacts["symbols"]:
        setups = artifacts["setups"].get(sym)
        if not setups:
            continue
        sd = artifacts["data"][sym]
        rs_ff = _ffill(artifacts["rs_pct"][sym])
        row = scan_symbol(sd, setups, rs_ff, artifacts["liq_masks"][sym],
                          market, cfg, asof_idx, equity, max_staleness_days)
        if row is None:
            continue
        row.asof = calendar[sd.last_idx]
        if require_gates and not (row.trend_template and row.liquidity_ok):
            continue
        rows.append(row)

    df = pd.DataFrame([vars(r) for r in rows])
    if df.empty:
        return df
    if cfg.entry.rank_by == "tightness":
        df = df.sort_values("final_depth_pct")
    elif cfg.entry.rank_by == "contractions":
        df = df.sort_values(["n_contractions", "rs_pct"], ascending=[False, False])
    else:
        df = df.sort_values("rs_pct", ascending=False)
    return df.reset_index(drop=True)
