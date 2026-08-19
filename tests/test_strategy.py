import numpy as np
import pandas as pd

from engine.indicators import enrich
from engine.strategy import Params, scan_signals, with_
from tests.conftest import BREAK, make_session


def test_signal_fires_on_textbook_pattern(enriched_rally, loose_params):
    sigs = scan_signals(enriched_rally, loose_params)
    assert BREAK in sigs, f"expected breakout bar {BREAK} in {sigs}"


def test_no_signal_without_breakout(loose_params):
    E = enrich(make_session(breakout=False))
    assert BREAK not in scan_signals(E, loose_params)


def test_no_lookahead_future_bars_irrelevant(loose_params):
    """Signals up to bar i must not depend on anything after bar i."""
    rally = enrich(make_session(after="rally"))
    dump = enrich(make_session(after="dump"))
    s1 = [s for s in scan_signals(rally, loose_params) if s <= BREAK]
    s2 = [s for s in scan_signals(dump, loose_params) if s <= BREAK]
    assert s1 == s2


def test_relvol_filter_blocks(loose_params):
    E = enrich(make_session())
    assert BREAK not in scan_signals(E, with_(loose_params, relvol_min=10.0))


def test_momentum_strength_filter_blocks(loose_params):
    E = enrich(make_session())
    assert BREAK not in scan_signals(E, with_(loose_params, momentum_min_gain_atr=50.0))


def test_time_window_blocks(loose_params):
    E = enrich(make_session())
    # breakout bar is at 09:30 + 63*5min = 14:45 -> entry_end 12:00 blocks it
    p = with_(loose_params, entry_end_min=12 * 60)
    assert BREAK not in scan_signals(E, p)


def test_rsi_filter_bounds(loose_params):
    E = enrich(make_session())
    p = with_(loose_params, rsi_filter=True, rsi_min=99.0, rsi_max=100.0)
    assert BREAK not in scan_signals(E, p)


def test_pullback_length_filter(loose_params):
    E = enrich(make_session())
    assert BREAK not in scan_signals(E, with_(loose_params, max_pullback_bars=1))
    assert BREAK in scan_signals(E, with_(loose_params, max_pullback_bars=3))


def test_scanner_filter_masks(loose_params):
    E = enrich(make_session())
    E["scan_ok"] = np.zeros(len(E["open"]), dtype=bool)
    p = with_(loose_params, scanner_filter=True)
    assert len(scan_signals(E, p)) == 0
    E["scan_ok"][:] = True
    assert BREAK in scan_signals(E, p)


def test_in_play_filter_needs_day_strength(loose_params):
    # single synthetic day has adr sentinel 99 -> day gain can never reach 0.3*99
    E = enrich(make_session())
    p = with_(loose_params, in_play_filter=True)
    assert BREAK not in scan_signals(E, p)


def test_no_signal_across_day_boundary(loose_params):
    """Pattern split across two sessions must not fire on day 2's open."""
    d1 = make_session("2026-08-10", after="chop")
    d2 = make_session("2026-08-11", after="rally")
    df = pd.concat([d1, d2])
    E = enrich(df)
    n1 = len(d1)
    sigs = scan_signals(E, loose_params)
    early_day2 = [s for s in sigs if n1 <= s < n1 + 8]
    assert not early_day2, f"signals fired within warmup bars of day 2: {early_day2}"


# ---------- entry_gates mirrors scan_signals ----------

def test_entry_gates_match_scan_signals():
    """All gates passing must be exactly equivalent to a signal on that bar.

    This is the guard against the dashboard's explanation drifting away from
    the logic that actually places orders.
    """
    from engine.strategy import entry_gates, scan_signals

    for after in ("rally", "dump", "chop"):
        for params in (Params(),
                       Params(macd_filter=True),
                       Params(require_above_vwap=False, max_pullback_bars=2),
                       Params(rsi_filter=True, relvol_min=1.0)):
            E = enrich(make_session(after=after))
            fired = set(scan_signals(E, params).tolist())
            for i in range(1, len(E["high"])):
                gates = entry_gates(E, params, i)
                assert gates, f"no gates produced for bar {i}"
                all_pass = all(ok for _, ok in gates)
                assert all_pass == (i in fired), (
                    f"bar {i} after={after}: gates say {all_pass}, "
                    f"scan_signals says {i in fired}; "
                    f"failing={[k for k, ok in gates if not ok]}")


def test_entry_gates_name_the_missing_condition():
    from engine.strategy import entry_gates
    E = enrich(make_session(after="rally"))
    # a bar with no breakout must report exactly that
    quiet = 20
    failing = [k for k, ok in entry_gates(E, Params(), quiet) if not ok]
    assert "surge" in failing or "breakout" in failing or "pullback" in failing
