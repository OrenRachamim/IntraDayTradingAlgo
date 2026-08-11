# Micro Pullback — Final Backtest Summary

Window: **2026-05-20 → 2026-08-07**  |  Universe: 30 symbols

## Chosen configuration

```json
{
  "timeframe": "15m",
  "require_above_vwap": true,
  "momentum_mode": "surge",
  "momentum_lookback": 4,
  "momentum_min_gain_atr": 1.5,
  "hod_dist_atr": 1.0,
  "hod_day_gain_atr": 2.5,
  "relvol_min": 1.3,
  "pullback_def": "lower_high",
  "min_pullback_bars": 1,
  "max_pullback_bars": 2,
  "rsi_filter": false,
  "rsi_min": 50.0,
  "rsi_max": 80.0,
  "macd_filter": true,
  "macd_rising": false,
  "stop_mode": "pullback_low",
  "stop_atr_mult": 1.0,
  "stop_pct": 0.5,
  "stop_cap_pct": 1.5,
  "target_mode": "rr",
  "target_rr": 3.0,
  "target_pct": 1.0,
  "trail_mode": "none",
  "trail_atr_mult": 1.5,
  "trail_pct": 0.3,
  "trail_activate_rr": 1.0,
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
| total_return_pct | 12.27 |
| ann_return_pct | 70.70 |
| n_trades | 45 |
| win_rate | 42.22 |
| profit_factor | 1.49 |
| expectancy_pct | 0.21 |
| max_dd_pct | -6.81 |
| sharpe | 1.89 |
| avg_bars_held | 6.09 |
| **SPY buy & hold (same window)** | 4.59% |
| **Edge vs SPY** | +7.68pp |

## Robustness (train 70% / validation 30% of days)

| timeframe   | require_above_vwap   | momentum_mode   |   momentum_lookback |   momentum_min_gain_atr |   hod_dist_atr |   hod_day_gain_atr |   relvol_min | pullback_def   |   min_pullback_bars |   max_pullback_bars | rsi_filter   |   rsi_min |   rsi_max | macd_filter   | macd_rising   | stop_mode    |   stop_atr_mult |   stop_pct |   stop_cap_pct | target_mode   |   target_rr |   target_pct | trail_mode   |   trail_atr_mult |   trail_pct |   trail_activate_rr |   entry_start_min |   entry_end_min |   eod_exit_min | sizing_mode   |   risk_per_trade_pct |   pos_leverage_cap |   max_concurrent |   full_ret |   full_pf |   train_ret |   train_pf |   train_trades |   val_ret |   val_pf |   val_trades | robust   |
|:------------|:---------------------|:----------------|--------------------:|------------------------:|---------------:|-------------------:|-------------:|:---------------|--------------------:|--------------------:|:-------------|----------:|----------:|:--------------|:--------------|:-------------|----------------:|-----------:|---------------:|:--------------|------------:|-------------:|:-------------|-----------------:|------------:|--------------------:|------------------:|----------------:|---------------:|:--------------|---------------------:|-------------------:|-----------------:|-----------:|----------:|------------:|-----------:|---------------:|----------:|---------:|-------------:|:---------|
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   2 | False        |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           3 |            1 | none         |              1.5 |         0.3 |                 1   |               575 |             930 |            955 | risk          |                  1.5 |                  2 |                2 |   12.2698  |   1.49149 |    17.671   |    1.90024 |             35 |  -6.81181 | 0.205533 |           10 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   2 | False        |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           3 |            1 | none         |              1.5 |         0.5 |                 1   |               575 |             930 |            955 | risk          |                  1.5 |                  2 |                2 |   12.2698  |   1.49149 |    17.671   |    1.90024 |             35 |  -6.81181 | 0.205533 |           10 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   2 | False        |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           3 |            1 | none         |              1.5 |         0.3 |                 0.5 |               575 |             930 |            955 | risk          |                  1.5 |                  2 |                2 |   12.2698  |   1.49149 |    17.671   |    1.90024 |             35 |  -6.81181 | 0.205533 |           10 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   2 | False        |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           3 |            1 | none         |              1.5 |         0.3 |                 0.5 |               575 |             930 |            955 | risk          |                  1.5 |                  1 |                2 |    9.22437 |   1.49149 |    11.7734  |    1.90024 |             35 |  -4.55581 | 0.205533 |           10 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   2 | False        |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           3 |            1 | none         |              1.5 |         0.5 |                 1   |               575 |             930 |            955 | risk          |                  1.5 |                  1 |                2 |    9.22437 |   1.49149 |    11.7734  |    1.90024 |             35 |  -4.55581 | 0.205533 |           10 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   2 | False        |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           3 |            1 | none         |              1.5 |         0.3 |                 1   |               575 |             930 |            955 | risk          |                  1.5 |                  1 |                2 |    9.22437 |   1.49149 |    11.7734  |    1.90024 |             35 |  -4.55581 | 0.205533 |           10 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   2 | False        |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           3 |            1 | none         |              1.5 |         0.3 |                 1   |               575 |             930 |            955 | risk          |                  1.5 |                  2 |                4 |    7.17275 |   1.21575 |    17.7295  |    1.61026 |             43 |  -6.81181 | 0.205533 |           10 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   2 | False        |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           3 |            1 | none         |              1.5 |         0.3 |                 0.5 |               575 |             930 |            955 | risk          |                  1.5 |                  2 |                4 |    7.17275 |   1.21575 |    17.7295  |    1.61026 |             43 |  -6.81181 | 0.205533 |           10 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   2 | False        |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           3 |            1 | none         |              1.5 |         0.5 |                 1   |               575 |             930 |            955 | risk          |                  1.5 |                  2 |                4 |    7.17275 |   1.21575 |    17.7295  |    1.61026 |             43 |  -6.81181 | 0.205533 |           10 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   2 | False        |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           3 |            1 | none         |              1.5 |         0.5 |                 1   |               575 |             930 |            955 | risk          |                  1   |                  1 |                2 |    7.51424 |   1.49149 |     9.92807 |    1.90024 |             35 |  -3.73765 | 0.205533 |           10 | False    |

*Generated automatically by run_backtest.py*