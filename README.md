# IntraDayTradingAlgo — Micro Pullback Engine

Intraday (same-day entry & exit, long-only) backtesting engine for the **Micro Pullback**
momentum-continuation strategy on liquid US equities, with data from **Yahoo Finance**.

## Strategy in one paragraph

Inside a confirmed intraday uptrend (price above session VWAP, EMA9 > EMA20), wait for a
strong momentum surge (≥ K×ATR move on elevated relative volume), then a *micro pullback*
of 1–3 consecutive lower-high bars. Enter long the moment a bar breaks the previous bar's
high (buy-stop), with the stop under the pullback low. Take profit at an R-multiple target
or ratchet a trailing stop, and always go flat by 15:55 ET. The goal: get in, capture the
next momentum leg quickly, get out, move to the next setup.

## Layout

```
engine/
  data.py        Yahoo Finance intraday fetcher (1m/5m/15m/30m) + parquet cache
  indicators.py  EMA, session VWAP, RSI, MACD, ATR, relative volume, pullback runs
  strategy.py    Params dataclass + vectorized Micro Pullback signal scanner
  backtest.py    conservative bar-by-bar trade simulator + portfolio compounding
  metrics.py     win rate, profit factor, drawdown, Sharpe, SPY benchmark
  optimize.py    staged grid-search optimizer with train/validation split
run_backtest.py  end-to-end pipeline (fetch -> 3 optimization iterations -> report)
PLAN.md          strategy research & full testing plan
results/         per-iteration grids, final trades, equity curve, SUMMARY.md
```

## Results (period 2026-05-15 → 2026-08-10, 30-symbol universe)

| Window | Strategy | SPY same window | Profit factor | Trades |
|---|---|---|---|---|
| Full period | **+6.24%** | +4.56% | 1.23 | 66 |
| Train (first 70% of days) | +1.97% | +2.09% | 1.17 | 48 |
| Validation (last 30%) | **+4.19%** | +3.31% | 1.44 | 18 |
| Walk-forward OOS (strict) | +1.20% | +5.43% | 1.21 | 28 |

The decisive discovery of the optimization campaign: the Micro Pullback edge only
exists on **stocks in play** — names whose day gain and cumulative volume are already
elevated versus their own norms (both computed without lookahead). Details, the full
iteration history, and honest caveats: `results/SUMMARY.md`.

## Run

```bash
pip install -r requirements.txt
python run_final_report.py      # evaluate the final chosen ensemble + write SUMMARY.md
python run_backtest.py          # full 4-iteration optimization pipeline from scratch
python run_walkforward.py       # strict walk-forward OOS evaluation
python run_inplay_scan.py       # focused robust scan around the in-play region
```

The pipeline automatically:
1. Downloads 1m (last ~30d) and 5m/15m/30m (last ~60d) bars for a 16-symbol
   high-beta universe + SPY benchmark, with on-disk caching.
2. **Iteration 1** optimizes risk geometry (timeframe × stop mode × target R × trailing).
3. **Iteration 2** optimizes signal quality (surge strength, relative volume, RSI,
   MACD, pullback length) around iteration-1 winners.
4. **Iteration 3** fine-tunes (targets, stop caps, trailing activation, session windows),
   then re-tests the top-10 on a 70/30 train/validation day split and picks a config
   that stays profitable out-of-sample.
5. Writes `results/SUMMARY.md` with full metrics vs SPY buy & hold over the same window.

## Modeling assumptions (deliberately conservative)

- Entry at `max(bar open, prev high + $0.01)` only if the bar traded through the trigger.
- If a bar spans both stop and target, the **stop is assumed to fill first**.
- 6 bps round-trip costs (slippage + commission) on every trade.
- Max 4 concurrent positions, each sized equity/4, $100k start, compounded.
- No shorts, no overnight positions, regular session only.

## Disclaimer

Backtests on ~1–2 months of Yahoo intraday history are a research tool, not a promise of
future returns. Intraday edges decay; validate on fresh data before risking capital.
