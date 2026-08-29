# Minervini VCP Backtesting System

A Python backtesting and research system implementing **Mark Minervini's VCP
(Volatility Contraction Pattern) / SEPA methodology** on daily US stock data,
combined with classical support/resistance principles.

- `docs/VCP_RESEARCH.md` — the deep-dive research and the quantified rule set.
- `docs/RESULTS.md` — backtest results, iteration log, and honest caveats.

**Result (2004-2026, 8 optimization rounds with walk-forward validation):**
the final config (`configs/final.yaml`) returns **+2,280% vs SPY's +914%
(2.49x profit multiple, CAGR 15.1% vs 10.8%)** using Minervini-style 2:1
margin — but the untouched 2022-2026 holdout *loses* money, so read
`docs/RESULTS.md` before believing any of it.

## What it does

1. Screens ~7,700 US stocks daily with Minervini's 8-point **Trend Template**
   (price vs 50/150/200-day SMAs, 52-week range position, RS percentile rank).
2. Detects **VCP bases**: successively tighter contractions, volume dry-up,
   pivot buy points — via causal swing-point analysis (no look-ahead).
3. Enters on **pivot breakouts** with buy-stop semantics, chase guard, and an
   optional S&P-500 market regime filter.
4. Manages risk Minervini-style: stop under the final contraction low (capped),
   risk-based position sizing, breakeven moves, sell-into-strength targets,
   and a moving-average trailing exit.
5. Runs portfolio-level backtests vs **SPY buy & hold**, and sweeps
   configurations with in-sample / out-of-sample validation.

## Setup

```bash
pip install -r requirements.txt
python scripts/prepare_data.py     # downloads ~530MB of daily OHLCV + SPY benchmark
```

Data sources: [paperswithbacktest/Stocks-Daily-Price](https://huggingface.co/datasets/paperswithbacktest/Stocks-Daily-Price)
(Hugging Face; check its license terms before commercial use) and Yahoo Finance
for the SPY benchmark.

## Usage

```bash
# single backtest with the textbook-default config
python scripts/run_backtest.py --config configs/base.yaml --out results/base

# override any parameter from the CLI
python scripts/run_backtest.py --config configs/base.yaml \
    --set risk.max_positions=5 --set exit.trail_ma=20

# run the optimization iterations (in-sample), then validate out-of-sample
python scripts/run_iterations.py

# tests
python -m pytest tests/ -q

# DAILY ENTRY SCANNER: today's live VCP buy-stop candidates per the final config
python scripts/scan_today.py --equity 100000 --refresh-universe
#   --refresh-universe  refresh every trend-template-passing symbol from Yahoo
#                       (~5-10 min) so setups formed since the monthly bulk
#                       update are found - the true daily mode
#   --no-refresh        offline mode (bulk data only)
# Output: ranked table + results/scans/scan_YYYY-MM-DD.csv with, per candidate:
#   trigger (buy-stop price), stop (initial stop), shares/position size for
#   your equity, pattern stats (contractions, tightness, volume dry-up),
#   RS percentile, and the market-regime state.
```

## Repository layout

```
vcp/                  the library
  config.py           all strategy parameters (dataclasses + YAML)
  data.py             per-symbol parquet -> calendar-aligned arrays
  indicators.py       SMA / rolling extrema / returns / swing points
  trend_template.py   Minervini trend template + cross-sectional RS ranks
  vcp_detector.py     contraction-sequence detection -> Setup objects
  backtester.py       portfolio-level daily simulator
  metrics.py          CAGR / Sharpe / MaxDD / profit multiple vs SPY
  pipeline.py         orchestration + caching for fast iteration sweeps
scripts/              prepare_data / run_backtest / run_iterations
configs/              YAML configs (base + iteration variants)
tests/                pytest suite (synthetic patterns, accounting, causality)
docs/                 research + results
```

## Honest limitations

Read `docs/VCP_RESEARCH.md` §8/§10 and `docs/RESULTS.md` before trusting any
number: the dataset has partial delisting coverage (survivorship bias),
fills are modeled on daily bars, and optimized parameters are validated
out-of-sample but past performance never guarantees future results.
This is a research tool, not investment advice.
