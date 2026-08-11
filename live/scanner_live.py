"""Live 10:00 ET morning scanner.

Candidates come from IBKR's native market scanner (top % gainers + most active,
pre-filtered by price and day volume). For each candidate we fetch today's 1m
bars plus yesterday's close and score exactly like the backtested scanner:

  gap        = today's open / yesterday's close - 1
  early_move = last price / today's open - 1
  early_rv   = today's cumulative volume vs the same-time average of the
               previous 10 sessions

Eligible if gap >= gap_min OR (early_move >= move_min AND early_rv >= rv_min);
score = max(gap,0) + max(early_move,0) + 0.1*early_rv; return the top K.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np

from .broker import Broker
from .config import LiveConfig
from .notify import get_logger
from . import state


def _early_relvol(df, now_minute: int) -> float:
    """Cumulative volume today vs same-elapsed-time average of prior sessions."""
    days = df.index.normalize()
    uniq = days.unique()
    if len(uniq) < 4:
        return 1.0
    today = uniq[-1]
    minute = df.index.hour * 60 + df.index.minute
    cum_today = df.loc[(days == today) & (minute <= now_minute), "Volume"].sum()
    prior = []
    for d in uniq[:-1][-10:]:
        v = df.loc[(days == d) & (minute <= now_minute), "Volume"].sum()
        if v > 0:
            prior.append(v)
    if not prior:
        return 1.0
    return float(cum_today / np.mean(prior))


def run_scanner(broker: Broker, cfg: LiveConfig) -> list[dict]:
    log = get_logger()
    symbols = broker.scan_candidates()
    log.info(f"scanner candidates from IBKR: {len(symbols)}")
    now = datetime.now()
    now_minute = now.hour * 60 + now.minute
    rows = []
    for sym in symbols:
        try:
            c = broker.stock(sym)
            df = broker.bars_df(broker.bars_1m(c, duration="12 D"))
            if len(df) < 500:
                continue
            days = df.index.normalize()
            today = days.unique()[-1]
            tdf = df[days == today]
            if len(tdf) < 5:
                continue
            day_open = float(tdf["Open"].iloc[0])
            last_px = float(tdf["Close"].iloc[-1])
            prev_close = broker.prev_close(c)
            gap = day_open / prev_close - 1 if np.isfinite(prev_close) and prev_close > 0 else 0.0
            early_move = last_px / day_open - 1
            early_rv = _early_relvol(df, now_minute)
            eligible = (gap >= cfg.gap_min) or \
                (early_move >= cfg.move_min and early_rv >= cfg.early_rv_min)
            if eligible:
                rows.append({"symbol": sym, "gap": gap, "early_move": early_move,
                             "early_rv": early_rv,
                             "score": max(gap, 0) + max(early_move, 0) + 0.1 * early_rv})
            broker.ib.sleep(0.2)
        except Exception as e:  # noqa: BLE001
            log.warning(f"scanner {sym} failed: {e}")
    rows.sort(key=lambda r: r["score"], reverse=True)
    picks = rows[:cfg.scanner_top_k]
    state.log_scanner(str(now.date()), picks)
    log.info("scanner picks: " + (", ".join(
        f"{p['symbol']}(gap {p['gap']:+.1%}, move {p['early_move']:+.1%}, "
        f"rv {p['early_rv']:.1f})" for p in picks) or "none"))
    return picks
