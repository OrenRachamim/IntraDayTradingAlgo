# Micro Pullback — Final Backtest Summary

Window: **2026-05-21 → 2026-08-07**  |  Universe: 16 symbols

## Chosen configuration

```json
{
  "timeframe": "15m",
  "require_above_vwap": true,
  "momentum_mode": "surge",
  "momentum_lookback": 4,
  "momentum_min_gain_atr": 1.5,
  "hod_dist_atr": 1.0,
  "hod_day_gain_atr": 1.5,
  "relvol_min": 1.3,
  "pullback_def": "lower_high",
  "min_pullback_bars": 1,
  "max_pullback_bars": 3,
  "rsi_filter": true,
  "rsi_min": 50.0,
  "rsi_max": 80.0,
  "macd_filter": true,
  "macd_rising": false,
  "stop_mode": "pullback_low",
  "stop_atr_mult": 1.0,
  "stop_pct": 0.5,
  "stop_cap_pct": 1.5,
  "target_mode": "rr",
  "target_rr": 2.0,
  "target_pct": 1.0,
  "trail_mode": "none",
  "trail_atr_mult": 1.5,
  "trail_pct": 0.5,
  "trail_activate_rr": 0.5,
  "entry_start_min": 575,
  "entry_end_min": 930,
  "eod_exit_min": 955
}
```

## Performance (portfolio, $100k start, max 4 concurrent, costs included)

| Metric | Value |
|---|---|
| total_return_pct | 0.60 |
| ann_return_pct | 2.85 |
| n_trades | 28 |
| win_rate | 39.29 |
| profit_factor | 1.24 |
| expectancy_pct | 0.09 |
| max_dd_pct | -0.76 |
| sharpe | 1.94 |
| avg_bars_held | 4.79 |
| **SPY buy & hold (same window)** | 4.38% |
| **Edge vs SPY** | -3.78pp |

## Robustness (train 70% / validation 30% of days)

| timeframe   | require_above_vwap   | momentum_mode   |   momentum_lookback |   momentum_min_gain_atr |   hod_dist_atr |   hod_day_gain_atr |   relvol_min | pullback_def   |   min_pullback_bars |   max_pullback_bars | rsi_filter   |   rsi_min |   rsi_max | macd_filter   | macd_rising   | stop_mode    |   stop_atr_mult |   stop_pct |   stop_cap_pct | target_mode   |   target_rr |   target_pct | trail_mode   |   trail_atr_mult |   trail_pct |   trail_activate_rr |   entry_start_min |   entry_end_min |   eod_exit_min |   full_ret |   full_pf |   train_ret |   train_pf |   train_trades |   val_ret |   val_pf |   val_trades | robust   |
|:------------|:---------------------|:----------------|--------------------:|------------------------:|---------------:|-------------------:|-------------:|:---------------|--------------------:|--------------------:|:-------------|----------:|----------:|:--------------|:--------------|:-------------|----------------:|-----------:|---------------:|:--------------|------------:|-------------:|:-------------|-----------------:|------------:|--------------------:|------------------:|----------------:|---------------:|-----------:|----------:|------------:|-----------:|---------------:|----------:|---------:|-------------:|:---------|
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                1.5 |          1.3 | lower_high     |                   1 |                   3 | True         |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           2 |            1 | none         |              1.5 |         0.5 |                 0.5 |               575 |             930 |            955 |   0.60283  |   1.23587 |    0.912919 |    1.40244 |             25 | -0.307284 |        0 |            3 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                1.5 |          1.3 | lower_high     |                   1 |                   3 | True         |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           2 |            1 | none         |              1.5 |         0.5 |                 1   |               575 |             930 |            955 |   0.60283  |   1.23587 |    0.912919 |    1.40244 |             25 | -0.307284 |        0 |            3 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                1.5 |          1.3 | lower_high     |                   1 |                   3 | True         |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           2 |            1 | none         |              1.5 |         0.3 |                 1   |               575 |             930 |            955 |   0.60283  |   1.23587 |    0.912919 |    1.40244 |             25 | -0.307284 |        0 |            3 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                1.5 |          1.3 | lower_high     |                   1 |                   3 | True         |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           2 |            1 | none         |              1.5 |         0.3 |                 0.5 |               575 |             930 |            955 |   0.60283  |   1.23587 |    0.912919 |    1.40244 |             25 | -0.307284 |        0 |            3 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   3 | True         |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           2 |            1 | none         |              1.5 |         0.3 |                 0.5 |               575 |             930 |            955 |   0.60283  |   1.23587 |    0.912919 |    1.40244 |             25 | -0.307284 |        0 |            3 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   3 | True         |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           2 |            1 | none         |              1.5 |         0.3 |                 1   |               575 |             930 |            955 |   0.60283  |   1.23587 |    0.912919 |    1.40244 |             25 | -0.307284 |        0 |            3 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   3 | True         |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           2 |            1 | none         |              1.5 |         0.5 |                 1   |               575 |             930 |            955 |   0.60283  |   1.23587 |    0.912919 |    1.40244 |             25 | -0.307284 |        0 |            3 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   3 | True         |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           2 |            1 | none         |              1.5 |         0.5 |                 0.5 |               575 |             930 |            955 |   0.60283  |   1.23587 |    0.912919 |    1.40244 |             25 | -0.307284 |        0 |            3 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   2 | True         |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           3 |            1 | none         |              1.5 |         0.3 |                 0.5 |               575 |             930 |            955 |   0.558498 |   1.23097 |    0.868451 |    1.40672 |             21 | -0.307284 |        0 |            3 | False    |
| 15m         | True                 | surge           |                   4 |                     1.5 |              1 |                2.5 |          1.3 | lower_high     |                   1 |                   2 | True         |        50 |        80 | True          | False         | pullback_low |               1 |        0.5 |            1.5 | rr            |           3 |            1 | none         |              1.5 |         0.3 |                 1   |               575 |             930 |            955 |   0.558498 |   1.23097 |    0.868451 |    1.40672 |             21 | -0.307284 |        0 |            3 | False    |

*Generated automatically by run_backtest.py*