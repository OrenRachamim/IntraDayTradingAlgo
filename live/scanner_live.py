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


import numpy as np

from .broker import Broker
from .config import LiveConfig, now_et
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


def _f(v) -> float:
    """A usable positive number from a ticker field, else nan."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return f if np.isfinite(f) and f > 0 else float("nan")


def _score(gap: float, early_move: float, early_rv: float) -> float:
    return max(gap, 0) + max(early_move, 0) + 0.1 * early_rv


RTH_MINUTES = 390
RTH_OPEN_MIN = 9 * 60 + 30


def _est_relvol(volume: float, avvolume: float, now_minute: int) -> float:
    """Stand-in for early relative volume, computed from a snapshot alone.

    Scales today's volume so far against the 90-day average daily volume by how
    much of the session has elapsed. Volume is front-loaded in the morning, so
    this overstates the true figure -- but by a factor common to every symbol
    at a given moment, which is all a ranking needs.

    It decides only which names are worth a historical request. Eligibility and
    the published score always use the real `_early_relvol` read from 1m bars.
    """
    if not (np.isfinite(volume) and np.isfinite(avvolume)):
        return 0.0
    elapsed = min(RTH_MINUTES, max(5, now_minute - RTH_OPEN_MIN))
    return (volume / avvolume) * (RTH_MINUTES / elapsed)


def _relvol_from(df, now, now_minute: int):
    """(early relative volume, session was stale), or None when data is thin.

    None means "drop this symbol", matching the original scan. Returning a
    neutral 1.0 instead would let a name whose history failed to arrive slip
    through the relative-volume gate on a made-up number.
    """
    if len(df) < 500:
        return None
    days = df.index.normalize()
    stale = days.unique()[-1].date() != now.date()
    return _early_relvol(df, now_minute), stale


def run_scanner(broker: Broker, cfg: LiveConfig) -> list[dict]:
    """Build the morning watchlist.

    Two stages, because IBKR allows only ~60 historical-data requests per ten
    minutes and the candidate list routinely exceeds 60 names:

    1. One market-data snapshot per chunk of candidates gives today's open, the
       last trade and the previous close -- enough for gap and early move, at
       zero historical-request cost.
    2. Only names that can still qualify (gap OR early-move branch) get their
       1m history pulled for relative volume, capped at `scanner_deep_max`.

    If snapshots come back empty -- no market-data entitlement, for instance --
    the original all-historical scan runs instead, on a capped candidate list.
    """
    log = get_logger()
    symbols = broker.scan_candidates()
    log.info(f"scanner candidates from IBKR: {len(symbols)}")
    now = now_et()
    now_minute = now.hour * 60 + now.minute
    if now_minute < 9 * 60 + 35:
        log.warning("⚠️ scanner invoked before 09:35 ET — no RTH bars for today yet; "
                    "any output below reflects the PREVIOUS session (research only)")

    contracts = broker.stocks(symbols)
    ticks = broker.snapshots(list(contracts.values())) if contracts else {}

    # stage 1 -- gap and early move from snapshots, no historical data
    prelim = []
    for sym, t in ticks.items():
        day_open = _f(t.get("open"))
        prev_close = _f(t.get("close"))
        last_px = _f(t.get("last"))
        if not np.isfinite(last_px):
            last_px = _f(t.get("market"))
        if not (np.isfinite(day_open) and np.isfinite(last_px)):
            continue
        gap = day_open / prev_close - 1 if np.isfinite(prev_close) else 0.0
        early_move = last_px / day_open - 1
        # superset of eligibility: rv can only matter on the early-move branch
        if gap >= cfg.gap_min or early_move >= cfg.move_min:
            prelim.append({"symbol": sym, "gap": gap, "early_move": early_move,
                           "est_rv": _est_relvol(_f(t.get("volume")),
                                                 _f(t.get("avvolume")), now_minute)})

    if not prelim and not ticks:
        log.warning("⚠️ no market-data snapshots — falling back to historical scan")
        return _scan_historical(broker, cfg, symbols[:cfg.scanner_deep_max],
                                now, now_minute, log)

    # Rank by the estimated score, not by gap+move alone: the real score is
    # dominated by its relative-volume term, so ignoring volume here would send
    # the deep scan after the wrong names.
    prelim.sort(key=lambda r: _score(r["gap"], r["early_move"], r["est_rv"]),
                reverse=True)
    deep, dropped = prelim[:cfg.scanner_deep_max], prelim[cfg.scanner_deep_max:]
    if dropped:
        log.info(f"scanner: {len(prelim)} names passed the snapshot screen, "
                 f"reading 1m history for the top {len(deep)} "
                 f"(dropped: " + ", ".join(f"{r['symbol']}~{r['est_rv']:.0f}"
                                           for r in dropped) + ")")

    # stage 2 -- relative volume for the survivors only, fetched concurrently
    bars = broker.bars_1m_many([contracts[r["symbol"]] for r in deep])
    rows, stale_sessions, thin = [], 0, []
    for r in deep:
        if r["symbol"] not in bars:
            continue                    # no history arrived -> not a candidate
        try:
            got = _relvol_from(broker.bars_df(bars[r["symbol"]]), now, now_minute)
        except Exception as e:  # noqa: BLE001
            log.warning(f"scanner {r['symbol']} failed: {e}")
            continue
        if got is None:
            thin.append(r["symbol"])
            continue
        rv, stale = got
        stale_sessions += stale
        if r["gap"] >= cfg.gap_min or \
                (r["early_move"] >= cfg.move_min and rv >= cfg.early_rv_min):
            rows.append({**r, "early_rv": rv,
                         "score": _score(r["gap"], r["early_move"], rv)})

    if thin:
        log.info(f"scanner: dropped {len(thin)} names with too little 1m history "
                 f"({', '.join(thin)})")
    if stale_sessions:
        log.warning(f"⚠️ {stale_sessions} symbols had no bars for today (ET) — "
                    "their features are from the previous session")
    return _finish(rows, cfg, now, log)


def _scan_historical(broker: Broker, cfg: LiveConfig, symbols: list[str],
                     now, now_minute: int, log) -> list[dict]:
    """Original scan: 1m bars plus a daily bar per symbol. Accurate but slow."""
    rows, stale_sessions = [], 0
    for sym in symbols:
        try:
            c = broker.stock(sym)
            df = broker.bars_df(broker.bars_1m(c, duration="12 D"))
            if len(df) < 500:
                continue
            days = df.index.normalize()
            today = days.unique()[-1]
            if today.date() != now.date():
                stale_sessions += 1     # market not open yet: last session is old
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
                             "score": _score(gap, early_move, early_rv)})
            broker.ib.sleep(0.2)
        except Exception as e:  # noqa: BLE001
            log.warning(f"scanner {sym} failed: {e}")
    if stale_sessions:
        log.warning(f"⚠️ {stale_sessions} symbols had no bars for today (ET) — "
                    "their features are from the previous session")
    return _finish(rows, cfg, now, log)


def _finish(rows: list[dict], cfg: LiveConfig, now, log) -> list[dict]:
    rows.sort(key=lambda r: r["score"], reverse=True)
    picks = rows[:cfg.scanner_top_k]
    if len(rows) > len(picks):
        # the watchlist is short by choice, not for lack of candidates
        log.info(f"scanner: {len(rows)} names eligible, keeping the top "
                 f"{cfg.scanner_top_k} (cut: " +
                 ", ".join(f"{r['symbol']} {r['score']:.2f}"
                           for r in rows[cfg.scanner_top_k:]) + ")")
    state.log_scanner(str(now.date()), picks)
    log.info("scanner picks: " + (", ".join(
        f"{p['symbol']}(gap {p['gap']:+.1%}, move {p['early_move']:+.1%}, "
        f"rv {p['early_rv']:.1f})" for p in picks) or "none"))
    return picks
