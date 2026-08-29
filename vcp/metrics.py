"""Performance metrics and benchmark comparison."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def cagr(equity: pd.Series) -> float:
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0 or equity.iloc[0] <= 0:
        return math.nan
    return (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float((equity / peak - 1.0).min())


def sharpe(equity: pd.Series) -> float:
    r = equity.pct_change().dropna()
    if r.std() == 0 or len(r) < 2:
        return math.nan
    return float(r.mean() / r.std() * math.sqrt(TRADING_DAYS))


def trade_stats(trades: list) -> dict:
    if not trades:
        return {"n_trades": 0}
    pnls = np.array([t.pnl for t in trades if not math.isnan(t.pnl)])
    rs = np.array([t.r_multiple for t in trades if not math.isnan(t.r_multiple)])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    return {
        "n_trades": len(pnls),
        "win_rate": float(len(wins) / len(pnls)) if len(pnls) else math.nan,
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else math.inf,
        "avg_R": float(rs.mean()) if len(rs) else math.nan,
        "total_pnl": float(pnls.sum()),
    }


def summarize(equity: pd.Series, trades: list, exposure: pd.Series,
              spy_close: pd.Series) -> dict:
    """Full summary including the benchmark profit multiple the project targets:
    profit_multiple = strategy net profit % / SPY buy&hold net profit % over the window."""
    spy = spy_close.reindex(equity.index).ffill().dropna()
    spy_ret = spy.iloc[-1] / spy.iloc[0] - 1.0
    strat_ret = equity.iloc[-1] / equity.iloc[0] - 1.0
    out = {
        "start": str(equity.index[0].date()),
        "end": str(equity.index[-1].date()),
        "final_equity": float(equity.iloc[-1]),
        "total_return": float(strat_ret),
        "cagr": cagr(equity),
        "sharpe": sharpe(equity),
        "max_drawdown": max_drawdown(equity),
        "avg_exposure": float(exposure.mean()),
        "spy_total_return": float(spy_ret),
        "spy_cagr": cagr(spy),
        "spy_max_drawdown": max_drawdown(spy),
        "profit_multiple_vs_spy": float(strat_ret / spy_ret) if spy_ret > 0 else math.inf,
    }
    out.update(trade_stats(trades))
    return out


def format_summary(s: dict) -> str:
    lines = [
        f"Period            : {s['start']} -> {s['end']}",
        f"Final equity      : ${s['final_equity']:>12,.0f}",
        f"Total return      : {s['total_return']*100:>8.1f}%   (SPY: {s['spy_total_return']*100:.1f}%)",
        f"CAGR              : {s['cagr']*100:>8.2f}%   (SPY: {s['spy_cagr']*100:.2f}%)",
        f"Sharpe            : {s['sharpe']:>8.2f}",
        f"Max drawdown      : {s['max_drawdown']*100:>8.1f}%   (SPY: {s['spy_max_drawdown']*100:.1f}%)",
        f"Avg exposure      : {s['avg_exposure']*100:>8.1f}%",
        f"PROFIT MULTIPLE   : {s['profit_multiple_vs_spy']:>8.2f}x  vs SPY buy&hold",
    ]
    if s.get("n_trades"):
        lines += [
            f"Trades            : {s['n_trades']}  |  win rate {s['win_rate']*100:.1f}%"
            f"  |  avg R {s['avg_R']:.2f}  |  PF {s['profit_factor']:.2f}",
            f"Avg win / loss    : ${s['avg_win']:,.0f} / ${s['avg_loss']:,.0f}",
        ]
    return "\n".join(lines)
