import numpy as np
import pandas as pd

from engine.indicators import enrich
from engine.optimize import _split_enriched, grid, prepare
from engine.scanner import SELECT_MINUTE, build_allowlist, day_features, inject_scan_ok
from engine.strategy import Params
from run_backtest import params_from_row, PARAM_COLS
from tests.conftest import make_session


def _multi_day_enriched(n_days=6, gap_day=None):
    frames = []
    base = 100.0
    for d in range(n_days):
        df = make_session(f"2026-08-{3 + d:02d}", after="chop")
        if gap_day is not None and d == gap_day:
            for c in ("Open", "High", "Low", "Close"):
                df[c] = df[c] + base * 0.10  # big gap day (~7% vs prior close)
            df["Volume"] = df["Volume"] * 3
        frames.append(df)
    return enrich(pd.concat(frames))


# ---------- optimize ----------

def test_split_enriched_partitions_days():
    E = _multi_day_enriched()
    cut = 4
    tr = _split_enriched(E, cut, "train")
    va = _split_enriched(E, cut, "validation")
    assert len(tr["open"]) + len(va["open"]) == len(E["open"])
    assert tr["day"].max() < cut <= va["day"].min()
    rng = _split_enriched(E, (2, 4))
    assert set(np.unique(rng["day"])) == {2, 3}
    assert len(rng["index"]) == len(rng["open"])


def test_grid_cartesian_product():
    g = grid(Params(), target_rr=[1.0, 2.0], trail_mode=["none", "pct"],
             relvol_min=[1.0, 1.5, 2.0])
    assert len(g) == 12
    assert len(set(g)) == 12                       # all distinct, hashable
    assert {p.target_rr for p in g} == {1.0, 2.0}


def test_params_from_row_roundtrip():
    p = Params(timeframe="15m", target_rr=2.5, macd_filter=True, relvol_min=1.7,
               scanner_filter=True, in_play_gain_adr=0.4)
    row = pd.Series({k: getattr(p, k) for k in PARAM_COLS})
    p2 = params_from_row(row)
    assert p2 == p
    # missing columns fall back to defaults
    row2 = row.drop(["scanner_filter", "relvol_min"])
    p3 = params_from_row(row2)
    assert p3.scanner_filter is False and p3.relvol_min == Params().relvol_min


def test_prepare_attaches_market_flag():
    df = make_session()
    spy = make_session()
    enriched = prepare({("TSLA", "5m"): df}, {"5m": spy})
    E = enriched[("TSLA", "5m")]
    assert "mkt_ok" in E and len(E["mkt_ok"]) == len(E["open"])
    assert E["mkt_ok"].dtype == bool


# ---------- scanner ----------

def test_day_features_gap_and_early_move():
    E = _multi_day_enriched(gap_day=4)
    feats = day_features({("SYN", "1m"): E})
    assert feats["day"].nunique() >= 5
    gap_row = feats[feats["day"] == 4].iloc[0]
    assert gap_row["gap"] > 0.04                    # big gap-up detected
    other = feats[feats["day"] == 3].iloc[0]
    assert other["gap"] < 0.0                       # synthetic days re-open lower


def test_build_allowlist_top_k_and_eligibility():
    feats = pd.DataFrame([
        {"symbol": "A", "day": 1, "gap": 0.05, "early_move": 0.00, "early_rv": 1.0},
        {"symbol": "B", "day": 1, "gap": 0.04, "early_move": 0.00, "early_rv": 1.0},
        {"symbol": "C", "day": 1, "gap": 0.00, "early_move": 0.03, "early_rv": 3.0},
        {"symbol": "D", "day": 1, "gap": 0.00, "early_move": 0.03, "early_rv": 1.0},  # rv too low
        {"symbol": "E", "day": 1, "gap": 0.01, "early_move": 0.00, "early_rv": 1.0},  # nothing
        {"symbol": "F", "day": 2, "gap": 0.10, "early_move": 0.00, "early_rv": 1.0},
    ])
    allow = build_allowlist(feats, gap_min=0.02, move_min=0.02, rv_min=2.0, top_k=2)
    assert ("D", 1) not in allow and ("E", 1) not in allow
    assert ("F", 2) in allow
    day1 = {s for s, d in allow if d == 1}
    assert len(day1) == 2 and "A" in day1           # top-2 by score


def test_inject_scan_ok_masks_time_and_symbol():
    E = _multi_day_enriched(n_days=3)
    enriched = {("SYN", "1m"): E}
    inject_scan_ok(enriched, {("SYN", 1)})
    ok = E["scan_ok"]
    assert ok.any()
    assert not ok[E["day"] != 1].any()                       # only the selected day
    sel_day = ok & (E["day"] == 1)
    assert not (E["minute"][sel_day] < SELECT_MINUTE).any()  # only after 10:00
