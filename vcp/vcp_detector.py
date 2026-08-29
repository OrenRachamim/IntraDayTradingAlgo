"""VCP detection: swing structure -> contraction sequence -> tradeable setups.

Causality: a swing point at bar s (window w) is only *known* at bar s + w.
A setup is emitted at its confirmation bar; the backtester monitors it from the
next bar onward, so no signal ever uses future data.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config
from .data import SymbolData
from .indicators import sma, swing_points


@dataclass
class Setup:
    symbol: str
    confirm_idx: int      # bar at which the setup became known
    pivot: float          # high of the final contraction (buy point)
    support_low: float    # low of the final contraction (invalidation / stop anchor)
    base_high: float
    base_start_idx: int
    n_contractions: int
    depths: tuple[float, ...]     # deepest-first contraction depths
    vdu_ratio: float              # final-leg volume / 50d avg volume


def _zigzag(sh: np.ndarray, sl: np.ndarray, high: np.ndarray, low: np.ndarray
            ) -> list[tuple[str, int, float]]:
    """Alternating swing sequence [('H', idx, price) | ('L', idx, price), ...].
    Consecutive same-type swings collapse to the more extreme one."""
    events: list[tuple[str, int, float]] = []
    hi_idx = np.flatnonzero(sh)
    lo_idx = np.flatnonzero(sl)
    merged = sorted([(i, "H", float(high[i])) for i in hi_idx]
                    + [(i, "L", float(low[i])) for i in lo_idx])
    for i, kind, price in merged:
        if events and events[-1][0] == kind:
            _, pi, pp = events[-1]
            if (kind == "H" and price > pp) or (kind == "L" and price < pp):
                events[-1] = (kind, i, price)
        else:
            events.append((kind, i, price))
    return events


def detect_setups(sd: SymbolData, cfg: Config) -> list[Setup]:
    v = cfg.vcp
    high = sd.high.astype(np.float64)
    low = sd.low.astype(np.float64)
    close = sd.close.astype(np.float64)
    n = len(close)
    sh, sl = swing_points(high, low, v.swing_window)
    zz = _zigzag(sh, sl, high, low)
    vol_sma50 = sma(sd.volume.astype(np.float64), 50)

    setups: list[Setup] = []
    # walk each swing low that closes a (H, L) leg
    for k in range(1, len(zz)):
        if zz[k][0] != "L" or zz[k - 1][0] != "H":
            continue
        s_idx = zz[k][1]
        c_idx = s_idx + v.swing_window
        if c_idx >= n or np.isnan(close[c_idx]):
            continue

        # legs ending here, most recent last: [(H_i, L_i), ...]
        legs: list[tuple[tuple[str, int, float], tuple[str, int, float]]] = []
        j = k
        while j >= 1 and len(legs) < v.max_contractions:
            if zz[j][0] == "L" and zz[j - 1][0] == "H":
                legs.append((zz[j - 1], zz[j]))
            j -= 2
        legs.reverse()
        if len(legs) < v.min_contractions:
            continue

        # find the longest suffix of legs forming a valid contraction sequence
        best: tuple[int, list[float], int] | None = None  # (n_c, depths, base_start)
        for n_c in range(v.max_contractions, v.min_contractions - 1, -1):
            if n_c > len(legs):
                continue
            sel = legs[-n_c:]
            if any(h[2] <= 0 or l[2] <= 0 for h, l in sel):
                continue
            depths = [(h[2] - l[2]) / h[2] for h, l in sel]
            if any(d <= 0 for d in depths):
                continue
            # tightening envelope: overall depth roughly halves first -> last,
            # with local noise tolerated between consecutive contractions
            if depths[-1] > v.contraction_ratio_max * depths[0]:
                continue
            if any(depths[i] > v.noise_tolerance * depths[i - 1]
                   for i in range(1, len(depths))):
                continue
            base_start = sel[0][0][1]
            base_len = s_idx - base_start
            if not (v.base_min_days <= base_len <= v.base_max_days):
                continue
            # measure the structure against its true top, not just the first swing
            struct_top = float(np.nanmax(high[base_start:s_idx + 1]))
            struct_low = float(np.nanmin(low[base_start:s_idx + 1]))
            if struct_top <= 0 or (struct_top - struct_low) / struct_top > v.base_max_depth:
                continue
            if depths[-1] > v.final_depth_max:
                continue
            pivot = sel[-1][0][2]
            if pivot < (1.0 - v.pivot_max_below_base_high) * struct_top:
                continue
            best = (n_c, depths, base_start)
            break
        if best is None:
            continue

        n_c, depths, base_start = best
        final_high_idx = legs[-1][0][1]
        pivot = legs[-1][0][2]
        support_low = legs[-1][1][2]

        # volume dry-up over the final leg
        leg_vol = sd.volume[final_high_idx:s_idx + 1].astype(np.float64)
        leg_vol = leg_vol[~np.isnan(leg_vol)]
        if len(leg_vol) == 0 or np.isnan(vol_sma50[s_idx]) or vol_sma50[s_idx] <= 0:
            continue
        vdu = float(leg_vol.mean() / vol_sma50[s_idx])
        if v.vdu_ratio_max > 0 and vdu > v.vdu_ratio_max:
            continue

        # breakout already happened during the confirmation lag -> missed
        trigger = pivot * (1.0 + cfg.entry.breakout_buffer)
        if np.nanmax(close[s_idx + 1:c_idx + 1]) > trigger:
            continue

        setups.append(Setup(
            symbol=sd.symbol, confirm_idx=c_idx, pivot=float(pivot),
            support_low=float(support_low), base_high=float(legs[-n_c][0][2]),
            base_start_idx=int(base_start), n_contractions=n_c,
            depths=tuple(round(d, 5) for d in depths), vdu_ratio=round(vdu, 4),
        ))
    return setups
