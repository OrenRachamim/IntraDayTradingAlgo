import numpy as np

from tests.helpers import make_symbol, ramp
from vcp.config import Config
from vcp.trend_template import liquidity_mask, rs_percentiles, trend_template_mask


def test_uptrend_passes_downtrend_fails():
    cfg = Config()
    up = make_symbol(ramp(20, 100, 400))
    down = make_symbol(ramp(100, 20, 400))
    rs_hi = np.full(400, 95.0, dtype=np.float32)
    assert trend_template_mask(up, rs_hi, cfg)[-1]
    assert not trend_template_mask(down, rs_hi, cfg)[-1]


def test_low_rs_fails():
    cfg = Config()
    up = make_symbol(ramp(20, 100, 400))
    rs_low = np.full(400, 10.0, dtype=np.float32)
    assert not trend_template_mask(up, rs_low, cfg)[-1]


def test_extended_below_52w_high_fails():
    cfg = Config()
    # rallies then falls 40% below its high -> criterion 7 violated
    path = ramp(20, 100, 300) + ramp(100, 60, 100)
    sd = make_symbol(path)
    rs = np.full(400, 95.0, dtype=np.float32)
    assert not trend_template_mask(sd, rs, cfg)[-1]


def test_rs_percentiles_orders_universe():
    strong = make_symbol(ramp(10, 100, 300), symbol="STRONG")
    weak = make_symbol(ramp(100, 50, 300), symbol="WEAK")
    flat = make_symbol([50.0] * 300, symbol="FLAT")
    data = {"STRONG": strong, "WEAK": weak, "FLAT": flat}
    # need >= 20 valid symbols for ranking; replicate
    for i in range(20):
        data[f"F{i}"] = make_symbol([50.0] * 300, symbol=f"F{i}")
    pct = rs_percentiles(sorted(data), data)
    assert pct["STRONG"][-1] > 95
    assert pct["WEAK"][-1] < 5
    assert 10 < pct["FLAT"][-1] < 90


def test_liquidity_mask():
    cfg = Config()
    liquid = make_symbol([50.0] * 100, volumes=[1e6] * 100)     # $50M/day
    thin = make_symbol([50.0] * 100, volumes=[1e3] * 100)       # $50K/day
    cheap = make_symbol([2.0] * 100, volumes=[10e6] * 100)      # $2 stock
    assert liquidity_mask(liquid, cfg)[-1]
    assert not liquidity_mask(thin, cfg)[-1]
    assert not liquidity_mask(cheap, cfg)[-1]
