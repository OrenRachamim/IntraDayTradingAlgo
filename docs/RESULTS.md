# Backtest Results & Iteration Log

> **Status: draft — final numbers pending round 8.**

This document reports every optimization round honestly: what was tried, what
worked in-sample (IS), and what survived out-of-sample (OOS). See
`results/iterations_round*.csv` for every individual run.

## Methodology

- Universe: ~6,100 US stocks with ≥1y history (paperswithbacktest daily OHLCV).
- Signals on split/dividend-adjusted daily bars; next-bar buy-stop fills;
  slippage 10bps + commission 2bps per side; margin interest 5.5%/yr when levered.
- Benchmark: SPY total-return buy & hold, same window and capital.
- Anti-overfitting evolution across rounds:
  - Round 2: single IS window (2004-2016) → **overfit** (IS 3.96x, OOS 0.03x).
  - Round 3+: min-across-subwindows selection.
  - Round 5+: walk-forward across three regime windows (2004-2010, 2010-2016,
    2016-2022) scored by worst-window CAGR edge; untouched holdout 2022-2026.

## Round-by-round summary

| Round | Change | IS result | OOS result | Verdict |
|-------|--------|-----------|------------|---------|
| 0 | Textbook defaults (base.yaml) | 0.11x | — | Profitable but far behind SPY |
| 1 | Greedy sweep, single window | 1.26x | 0.35x (2017-26) | Weak transfer |
| 2 | Continued greedy | 3.96x | 0.03x | Textbook overfit — rejected |
| — | Detector rewrite: noise-tolerant tightening envelope (census: strict monotonicity rejected ~99% of real bases in leaders) | | | Structural fix |
| 3 | Robust min-across-2-subwindows | 1.15x min | 0.28x | Real but modest |
| 4 | Second greedy pass | 1.77x min | 0.19x | Pre-2017 windows don't generalize |
| 5 | Walk-forward 3 windows, CAGR-edge score, 2:1 margin | min edge +8.1%/yr | **FULL 2.49x**, holdout -36% | Margin amplifies overfit |
| 6 | Leverage policies + core-satellite | — | — | SPY-parked cash ate 2008; regime-gated |
| 7 | Regime-gated SPY parking | — | — | No IS improvement |
| 8 | Failed-breakout fast exit | TBD | TBD | TBD |

## Honest caveats

1. **Survivorship bias**: only ~700 of 7,764 symbols end before 2026 — most
   stocks delisted during 2004-2020 are missing from the dataset, which
   inflates long-window results for stock-picking strategies (SPY benchmark
   is unaffected).
2. **Daily-bar fills**: buy-stops and stops modeled on OHLC; intraday paths
   are unknowable. Volume confirmation uses same-day volume (mild look-ahead,
   standard practice; disable with `entry.bo_vol_mult: 0`).
3. **Capacity**: compounding to millions makes 25-35% positions exceed what
   small-cap liquidity absorbs; results overstate what large accounts could do.
4. **Regime dependence**: the strategy's raw edge (PF, win rate) deteriorates
   sharply after ~2021 in this dataset — the "breakout-fakeout" era. Any
   forward use should paper-trade first and treat the market filter as
   load-bearing.
5. **Past performance does not predict future results.** This is a research
   tool, not investment advice.
