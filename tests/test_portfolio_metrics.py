import pandas as pd
import pytest

from engine.backtest import Trade, run_portfolio
from engine.metrics import compute_metrics, spy_benchmark


def T(sym, e_h, x_h, ret, risk=1.0, day=10):
    ts = lambda h, m=0: pd.Timestamp(f"2026-08-{day:02d} {h:02d}:{m:02d}", tz="US/Eastern")
    return Trade(sym, ts(e_h), ts(x_h), 100.0, 100 * (1 + ret / 100), ret, 5, "target", risk)


def test_max_concurrent_enforced():
    trades = [T("A", 10, 12, 1.0), T("B", 10, 12, 1.0), T("C", 11, 12, 1.0)]
    curve, df = run_portfolio(trades, max_concurrent=2)
    assert df["taken"].tolist() == [True, True, False]
    # after A and B exit at 12:00, a later trade is taken again
    trades.append(T("D", 13, 14, 1.0))
    _, df2 = run_portfolio(trades, max_concurrent=2)
    assert df2["taken"].tolist() == [True, True, False, True]


def test_notional_sizing_math():
    curve, df = run_portfolio([T("A", 10, 12, 2.0)], start_equity=100_000,
                              max_concurrent=4, sizing_mode="notional")
    # alloc = 25k, pnl = 25k * 2% = 500
    assert abs(curve.iloc[-1] - 100_500) < 1e-6


def test_risk_sizing_and_leverage_cap():
    # risk 1% of equity, stop distance 0.5% -> notional 2x equity, uncapped at 2.5
    curve, _ = run_portfolio([T("A", 10, 12, 1.0, risk=0.5)], start_equity=100_000,
                             sizing_mode="risk", risk_per_trade_pct=1.0,
                             pos_leverage_cap=2.5)
    assert abs(curve.iloc[-1] - 102_000) < 1e-6      # 200k notional * 1%
    # stop 0.2% -> 5x notional, capped at 2.5x
    curve2, _ = run_portfolio([T("A", 10, 12, 1.0, risk=0.2)], start_equity=100_000,
                              sizing_mode="risk", risk_per_trade_pct=1.0,
                              pos_leverage_cap=2.5)
    assert abs(curve2.iloc[-1] - 102_500) < 1e-6     # 250k notional * 1%


def test_compounding_across_days():
    trades = [T("A", 10, 12, 10.0, day=10), T("B", 10, 12, 10.0, day=11)]
    curve, _ = run_portfolio(trades, start_equity=100_000, max_concurrent=1,
                             sizing_mode="notional")
    assert abs(curve.iloc[-1] - 121_000) < 1e-6      # 100k -> 110k -> 121k
    assert len(curve) == 2


def test_metrics_math():
    trades = [T("A", 10, 11, 1.0), T("B", 11, 12, 1.0), T("C", 12, 13, -0.5),
              T("D", 13, 14, -0.5)]
    curve, df = run_portfolio(trades, max_concurrent=1, sizing_mode="notional")
    m = compute_metrics(curve, df)
    assert m["n_trades"] == 4
    assert abs(m["win_rate"] - 50.0) < 1e-9
    assert abs(m["profit_factor"] - 2.0) < 1e-9
    assert abs(m["expectancy_pct"] - 0.25) < 1e-9
    assert abs(m["avg_win_pct"] - 1.0) < 1e-9
    assert abs(m["avg_loss_pct"] + 0.5) < 1e-9
    assert m["max_dd_pct"] <= 0


def test_metrics_empty():
    m = compute_metrics(pd.Series(dtype=float), pd.DataFrame())
    assert m["n_trades"] == 0 and m["score"] == -999.0


def test_spy_benchmark_window():
    idx = pd.date_range("2026-08-03", periods=10, freq="B")
    spy = pd.DataFrame({"Close": [100 + i for i in range(10)]}, index=idx)
    start = pd.Timestamp("2026-08-03", tz="US/Eastern")
    end = pd.Timestamp("2026-08-14", tz="US/Eastern")
    r = spy_benchmark(spy, start, end)
    assert abs(r - 9.0) < 1e-9  # 100 -> 109
