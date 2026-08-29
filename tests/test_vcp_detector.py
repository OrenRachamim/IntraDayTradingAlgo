import numpy as np

from tests.helpers import make_symbol, ramp, vcp_price_path
from vcp.config import Config
from vcp.vcp_detector import detect_setups


def test_detects_textbook_vcp():
    closes, vols = vcp_price_path()
    sd = make_symbol(closes, volumes=vols)
    cfg = Config()
    setups = detect_setups(sd, cfg)
    assert setups, "textbook VCP should be detected"
    s = setups[-1]
    assert s.n_contractions >= 2
    # pivot is the high of the final contraction (close 96 * (1+spread))
    assert abs(s.pivot - 96 * 1.01) / s.pivot < 0.02
    # depths must be contracting
    for a, b in zip(s.depths, s.depths[1:]):
        assert b <= cfg.vcp.contraction_ratio_max * a + 1e-9
    assert s.vdu_ratio <= cfg.vcp.vdu_ratio_max


def test_rejects_widening_pattern():
    # contractions get WIDER (5% -> 12% -> 25%): loosening, not a VCP
    closes = ramp(50, 100, 60)
    closes += ramp(100, 95, 8)[1:] + ramp(95, 99, 8)[1:]
    closes += ramp(99, 87, 9)[1:] + ramp(87, 97, 9)[1:]
    closes += ramp(97, 73, 10)[1:] + ramp(73, 90, 10)[1:]
    sd = make_symbol(closes)
    assert detect_setups(sd, Config()) == []


def test_rejects_no_volume_dryup():
    closes, _ = vcp_price_path()
    rising_vols = list(np.linspace(1e6, 5e6, len(closes)))  # volume EXPANDS into the low
    sd = make_symbol(closes, volumes=rising_vols)
    assert detect_setups(sd, Config()) == []


def test_rejects_deep_base():
    # first leg down 60% -> exceeds base_max_depth
    closes = ramp(50, 100, 60)
    closes += ramp(100, 40, 15)[1:] + ramp(40, 90, 15)[1:]
    closes += ramp(90, 80, 9)[1:] + ramp(80, 88, 9)[1:]
    closes += ramp(88, 85, 8)[1:] + ramp(85, 86, 8)[1:]
    sd = make_symbol(closes)
    assert detect_setups(sd, Config()) == []


def test_setup_is_causal():
    closes, vols = vcp_price_path()
    sd = make_symbol(closes, volumes=vols)
    setups = detect_setups(sd, Config())
    for s in setups:
        # a setup's confirmation bar must be swing_window bars after its final low,
        # and everything it references must be in the past
        assert s.confirm_idx < len(closes)
        assert s.base_start_idx < s.confirm_idx
