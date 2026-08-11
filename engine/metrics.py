"""Performance metrics for a set of trades / equity curve."""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_metrics(curve: pd.Series, trades: pd.DataFrame, start_equity: float = 100_000.0,
                    period_days: float | None = None) -> dict:
    out = {
        "n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "expectancy_pct": 0.0,
        "avg_win_pct": 0.0, "avg_loss_pct": 0.0, "total_return_pct": 0.0,
        "ann_return_pct": 0.0, "max_dd_pct": 0.0, "sharpe": 0.0, "avg_bars_held": 0.0,
        "score": -999.0,
    }
    if trades is None or not len(trades):
        return out
    t = trades[trades["taken"]] if "taken" in trades else trades
    if not len(t):
        return out
    r = t["ret_pct"].to_numpy()
    wins, losses = r[r > 0], r[r <= 0]
    gross_w, gross_l = wins.sum(), -losses.sum()
    out["n_trades"] = int(len(r))
    out["win_rate"] = 100.0 * len(wins) / len(r)
    out["profit_factor"] = float(gross_w / gross_l) if gross_l > 0 else float("inf")
    out["expectancy_pct"] = float(r.mean())
    out["avg_win_pct"] = float(wins.mean()) if len(wins) else 0.0
    out["avg_loss_pct"] = float(losses.mean()) if len(losses) else 0.0
    out["avg_bars_held"] = float(t["bars_held"].mean())

    if curve is not None and len(curve):
        eq = pd.concat([pd.Series([start_equity]), curve])
        total = curve.iloc[-1] / start_equity - 1
        out["total_return_pct"] = 100.0 * total
        ndays = period_days or max((curve.index[-1] - curve.index[0]).days, 1)
        out["ann_return_pct"] = 100.0 * ((1 + total) ** (365.0 / ndays) - 1)
        run_max = eq.cummax()
        out["max_dd_pct"] = 100.0 * float(((eq - run_max) / run_max).min())
        dr = curve.pct_change().dropna()
        if len(dr) > 2 and dr.std() > 0:
            out["sharpe"] = float(dr.mean() / dr.std() * np.sqrt(252))
    # objective: return minus drawdown penalty; gated on sample size & PF
    if out["n_trades"] >= 30 and out["profit_factor"] > 1.0:
        out["score"] = out["total_return_pct"] + 0.5 * out["max_dd_pct"]  # max_dd is negative
    else:
        out["score"] = out["total_return_pct"] + 0.5 * out["max_dd_pct"] - 50.0
    return out


def spy_benchmark(spy_daily: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    """SPY buy & hold total return (%) over [start, end]."""
    c = spy_daily["Close"]
    c = c[(c.index >= start.tz_localize(None) if c.index.tz is None else c.index >= start)]
    c = c[c.index <= (end.tz_localize(None) if c.index.tz is None else end)]
    if len(c) < 2:
        return 0.0
    return 100.0 * (float(c.iloc[-1]) / float(c.iloc[0]) - 1)
