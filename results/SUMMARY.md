# Micro Pullback — Final Backtest Summary

Window: **2026-05-19 → 2026-08-07**  |  Universe: 30 symbols

## Chosen configuration

```json
{
  "timeframe": "30m",
  "require_above_vwap": true,
  "momentum_mode": "either",
  "momentum_lookback": 4,
  "momentum_min_gain_atr": 1.5,
  "hod_dist_atr": 1.0,
  "hod_day_gain_atr": 2.0,
  "relvol_min": 1.0,
  "pullback_def": "red_or_lh",
  "min_pullback_bars": 1,
  "max_pullback_bars": 2,
  "pullback_hold_ema": true,
  "market_filter": true,
  "rsi_filter": false,
  "rsi_min": 50.0,
  "rsi_max": 80.0,
  "macd_filter": false,
  "macd_rising": false,
  "stop_mode": "atr",
  "stop_atr_mult": 1.0,
  "stop_pct": 0.5,
  "stop_cap_pct": 1.5,
  "target_mode": "rr",
  "target_rr": 3.0,
  "target_pct": 1.0,
  "trail_mode": "none",
  "trail_atr_mult": 1.5,
  "trail_pct": 0.3,
  "trail_activate_rr": 0.5,
  "entry_start_min": 575,
  "entry_end_min": 930,
  "eod_exit_min": 955,
  "sizing_mode": "risk",
  "risk_per_trade_pct": 1.5,
  "pos_leverage_cap": 2.0,
  "max_concurrent": 2
}
```

## Performance (portfolio, $100k start, max 4 concurrent, costs included)

| Metric | Value |
|---|---|
| total_return_pct | 14.56 |
| ann_return_pct | 85.91 |
| n_trades | 42 |
| win_rate | 54.76 |
| profit_factor | 1.87 |
| expectancy_pct | 0.27 |
| max_dd_pct | -6.21 |
| sharpe | 3.55 |
| avg_bars_held | 2.57 |
| **SPY buy & hold (same window)** | 5.66% |
| **Edge vs SPY** | +8.90pp |

## Robustness (train 70% / validation 30% of days)

| timeframe   | require_above_vwap   | momentum_mode   |   momentum_lookback |   momentum_min_gain_atr |   hod_dist_atr |   hod_day_gain_atr |   relvol_min | pullback_def   |   min_pullback_bars |   max_pullback_bars | pullback_hold_ema   | market_filter   | rsi_filter   |   rsi_min |   rsi_max | macd_filter   | macd_rising   | stop_mode   |   stop_atr_mult |   stop_pct |   stop_cap_pct | target_mode   |   target_rr |   target_pct | trail_mode   |   trail_atr_mult |   trail_pct |   trail_activate_rr |   entry_start_min |   entry_end_min |   eod_exit_min | sizing_mode   |   risk_per_trade_pct |   pos_leverage_cap |   max_concurrent |   full_ret |   full_pf |   train_ret |   train_pf |   train_trades |   val_ret |   val_pf |   val_trades | robust   |
|:------------|:---------------------|:----------------|--------------------:|------------------------:|---------------:|-------------------:|-------------:|:---------------|--------------------:|--------------------:|:--------------------|:----------------|:-------------|----------:|----------:|:--------------|:--------------|:------------|----------------:|-----------:|---------------:|:--------------|------------:|-------------:|:-------------|-----------------:|------------:|--------------------:|------------------:|----------------:|---------------:|:--------------|---------------------:|-------------------:|-----------------:|-----------:|----------:|------------:|-----------:|---------------:|----------:|---------:|-------------:|:---------|
| 30m         | True                 | either          |                   4 |                     1.5 |              1 |                  2 |            1 | red_or_lh      |                   1 |                   2 | True                | True            | False        |        50 |        80 | False         | False         | atr         |               1 |        0.5 |            1.5 | rr            |           3 |            1 | none         |              1.5 |         0.3 |                 0.5 |               575 |             930 |            955 | risk          |                  1.5 |                  2 |                2 |    14.5577 |   1.86551 |     14.3268 |    1.95193 |             32 | -0.219149 |   1.0063 |           10 | False    |
| 30m         | True                 | either          |                   4 |                     1.5 |              1 |                  2 |            1 | red_or_lh      |                   1 |                   2 | True                | True            | False        |        50 |        80 | False         | False         | atr         |               1 |        0.5 |            1.5 | rr            |           3 |            1 | none         |              1.5 |         0.3 |                 1   |               575 |             930 |            955 | risk          |                  1.5 |                  2 |                2 |    14.5577 |   1.86551 |     14.3268 |    1.95193 |             32 | -0.219149 |   1.0063 |           10 | False    |
| 30m         | True                 | either          |                   4 |                     1.5 |              1 |                  2 |            1 | red_or_lh      |                   1 |                   2 | True                | True            | False        |        50 |        80 | False         | False         | atr         |               1 |        0.5 |            1   | rr            |           3 |            1 | none         |              1.5 |         0.3 |                 0.5 |               575 |             930 |            955 | risk          |                  1.5 |                  2 |                2 |    14.5577 |   1.86551 |     14.3268 |    1.95193 |             32 | -0.219149 |   1.0063 |           10 | False    |

*Generated automatically by run_backtest.py*