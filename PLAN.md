# Intraday Micro Pullback Trading Engine — Research & Testing Plan

## 1. Strategy selection

Surveyed leading intraday strategies:

| Strategy | Edge source | Verdict |
|---|---|---|
| **Micro Pullback** (momentum continuation) | Enter on the first new high after a 1–3 bar pullback inside a strong intraday uptrend. Popularized by Ross Cameron / Warrior Trading. Small risk (pullback low), fast resolution, high trade frequency. | **Chosen** — best fit for "enter fast, exit fast, jump to next position", works on 1m–15m bars, quantifiable with OHLCV only. |
| ORB (Opening Range Breakout) | Break of first 15/30-min range | Good but only ~1 signal/day/symbol; slower compounding |
| VWAP mean-reversion | Fade extensions back to VWAP | Counter-trend; worse tail risk intraday |
| Gap & Go | Momentum on gappers | Needs pre-market data & scanner; Yahoo intraday pre-market data unreliable |
| Scalping order-flow | Bid/ask imbalance | Needs L2 data — not available from Yahoo |

**Micro Pullback definition used here (long-only, flat by close):**
1. **Trend filter**: price above VWAP, EMA(fast) > EMA(slow).
2. **Momentum leg (surge)**: recent bar advanced ≥ K×ATR over a short lookback with relative volume ≥ threshold.
3. **Pullback**: 1–3 consecutive lower-high bars after the surge, holding above trend support.
4. **Entry**: first bar to break the prior bar's high (buy-stop at prev high + $0.01).
5. **Exit**: stop under pullback low (capped) / ATR / %, profit target (R-multiple or %), optional trailing stop after ≥1R, hard EOD flat at 15:55 ET.

## 2. Data

- Source: **Yahoo Finance** chart API via `yfinance` (custom `requests` session, on-disk parquet cache).
- Timeframes: **1m** (last ~30 days, fetched in ≤8-day chunks), **5m / 15m / 30m** (last ~60 days).
- Regular trading hours only. Universe: ~20 liquid, high-beta US large/mid caps (TSLA, NVDA, AMD, PLTR, META, COIN, SMCI, …) + **SPY** as benchmark.

## 3. Backtest engine rules (conservative)

- Entry at `max(bar open, prev_high + 0.01)`; only if the bar actually trades through the trigger.
- Intrabar ambiguity resolved **pessimistically**: if stop and target are both inside a bar's range, the stop is assumed to fill first.
- Trailing stop ratchets on highest high since entry; activated only after the trade is ≥ `activate_rr` × initial risk in profit.
- All positions force-closed at the 15:55 ET bar close. No overnight exposure, no shorts.
- Costs: 2 bps slippage + 1 bp commission **per side** (≈6 bps round trip) on every trade.
- One open trade per symbol; portfolio level: max 4 concurrent positions, each sized equity/4, compounded.

## 4. Parameter space tested

| Dimension | Values |
|---|---|
| Timeframe | 1m, 5m, 15m, 30m |
| Stop mode | pullback-low (capped), ATR×{1.0, 1.5}, pct {0.3%, 0.5%, 1.0%} |
| Target | R-multiple {1.5, 2, 3}, pct {0.5%, 1%, 2%} |
| Trailing stop | none, ATR×{1.0, 1.5}, pct {0.3%, 0.5%}; activate after 1R |
| Surge strength | {1.0, 1.5, 2.0} × ATR over 3–5 bars |
| Relative volume | ≥ {1.0, 1.3, 1.5, 2.0} (vs 20-bar average) |
| RSI(14) filter | off / on with bounds (50–80) |
| MACD (12,26,9) filter | off / hist > 0 / hist > 0 and rising |
| Pullback length | 1–2, 1–3 bars |
| Entry window | 09:35–15:30, 09:45–12:00, 09:35–11:30 & 14:00–15:30 |

Full cartesian grid is ~10⁵ combos — instead we run **staged (iterative) optimization**:

1. **Iteration 1 — risk geometry**: coarse grid over timeframe × stop × target × trailing with neutral filters. Keep top configs by score.
2. **Iteration 2 — signal quality**: grid over surge/relvol/RSI/MACD/pullback-length/time-window around the iteration-1 winners.
3. **Iteration 3 — fine-tune + robustness**: refine best region; **train/validation split** (first ~70% of days → train, last ~30% → validation) to reject overfit configs; final run on full period vs SPY buy & hold.

**Score** = total return − 0.5 × max drawdown, with hard gates: ≥ 30 trades, profit factor > 1.0.
Reported metrics: total & annualized return, win rate, profit factor, expectancy/trade, max DD, daily Sharpe, # trades, avg holding time, SPY benchmark return over the identical window.

## 5. Deliverables

- `engine/` — data, indicators, strategy, backtest, portfolio, optimizer modules.
- `run_backtest.py` — end-to-end pipeline (fetch → optimize iteratively → validate → report).
- `results/` — per-iteration CSV grids + `SUMMARY.md` with the final chosen configuration and benchmark comparison.
