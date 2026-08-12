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

### 1-minute timeframe study (window 2026-07-13 → 2026-08-10, 21 trading days)

A dedicated 300-config study (`run_1m_study.py`) showed 1m demands much stricter
signal quality than 5m/15m: **relvol ≥ 1.7, MACD histogram > 0, strict lower-high
pullback ≤ 2 bars, surge mode, 3R target, no trailing, full-day entries**. With
those filters (and the in-play gate):

| Same 21-day window | Return | PF | Trades | Max DD |
|---|---|---|---|---|
| Best 1m config | **+9.56%** | 1.34 | 55 | -4.07% |
| 1m runner-up (+RSI filter) | +9.16% | 1.35 | 53 | -4.07% |
| 5m+15m production ensemble | +6.45% | 1.61 | 22 | — |
| SPY buy & hold | +2.82% | — | — | — |

The runner-up stays profitable on both the train (+7.8%, PF 1.47) and validation
(+1.3%, PF 1.16) day-splits, and the edge survives costs (PF 1.75 gross → 1.35 at
6 bps). Loose 1m variants (relvol ≤ 1.3, no MACD, ≤2R targets) lose consistently —
which is why the earlier quick scan rejected the timeframe. Caveat: only ~21 trading
days of 1m history exist on Yahoo, so this is thinner evidence than the 59-day
5m/15m results; the production ensemble remains 5m+15m.

### Broad-universe test: the 1m edge does NOT generalize (important negative result)

Running the same robust 1m ensemble on a broad volume-filtered universe — S&P 500 +
Nasdaq-100 + liquid extras, avg dollar volume ≥ $150M/day, price ≥ $5, top 150 by
liquidity (`run_1m_broad.py`, `engine/universe.py`) — **loses -28.25%** (PF 0.77,
257 trades) over the same window where the curated universe made +9.56%.

Per-symbol diagnosis (`results/broad1m_*.csv`): the original high-beta names stay
positive inside the broad run (+0.024%/trade across 49 trades), while the ~120
added index names lose broadly and uniformly (-0.072%/trade across 212 trades —
MPWR, WDC, MRVL, CRM, CMCSA…, no single outlier). Volatility gates don't rescue it:
requiring avg daily range ≥ 4% improves PF only to 0.86, and tightening the in-play
gates on top makes it *worse* (PF 0.11–0.54) — extreme days in institutional names
are exactly where pullback-buying gets run over.

**Conclusion:** volume filtering alone does not define the tradable universe. The
micro-pullback edge lives specifically in retail-heavy, high-beta momentum names
(the curated 30-symbol list), not in the broad index universe.

### Morning scanner: dynamic in-play selection (`engine/scanner.py`)

Static symbol features (ADR, gap frequency, volume volatility, dollar volume) turned
out NOT to separate winners from losers (correlations ≈ 0 — the profitable "profile"
is a **day-state**, not a symbol property). So the scanner works like a trader's
10:00 ET watchlist build, using only information available at selection time:
opening gap vs yesterday's close, move from open to 10:00, and early relative
volume. Eligible = gap ≥ 3% OR (early move ≥ 2% AND early relvol ≥ 2); rank by
score, keep top-K per day; trades allowed only on picks, only after 10:00.

Results over the 21-day 1m window (SPY +4.03%):

| Setup | Return | PF | Win rate | Trades | Max DD |
|---|---|---|---|---|---|
| Broad 150, no scanner | -28.25% | 0.77 | 28% | 257 | -37.4% |
| Broad 150 + scanner K=5 | **+4.21%** | 1.14 | 40% | 40 | -5.7% |
| Curated 30 + scanner K=3 | **+6.62%** | 1.62 | 47% | 15 | **-1.6%** |
| Curated 30, no scanner | +9.56% | 1.34 | 36% | 55 | -4.1% |

Selectivity is everything: K=5 works, K=8/12 lose again. The scanner turns the
broad universe from disastrous to market-beating, and on the curated universe it
gives the best risk-adjusted result of the whole project (return/maxDD ≈ 4.1,
PF 1.62). Larger K dilutes straight back into the noise.

### Market/sector regime gates at entry: tested, NOT adopted

`run_regime_filters.py` tested hard entry gates — SPY>VWAP, QQQ>VWAP, the
symbol's own sector ETF>VWAP (SMH/IBIT/XLK/XLY/XLF/XLC), and combinations — on
both production engines (`results/regime_filters.csv`):

- **5m+15m ensemble**: every gate slashed full-period return (+6.24% baseline →
  -5.7%…-1.1%), all the damage in the train period; validation improved
  (QQQ+sector: +7.73%, PF 3.36, but n=9).
- **1m curated**: SPY gate improved PF (1.31→1.47) and drawdown (-5.8→-4.0%) at
  similar return, but flipped validation negative; sector gates hurt everywhere.

The effects flip sign between sub-periods on both engines — noise, not edge.
Structural read: the system already validates regime at the *stock* level (the
in-play/scanner gates demand the individual name be exceptionally strong today);
in-play momentum is largely idiosyncratic, so broad-market gates mostly delete
good trades (a stock ripping on its own news while SPY is red). `market_filter`
remains available in `Params` but stays **off** in production. If market state is
ever used, prefer it for position *sizing*, not binary entry blocking — and only
after it proves itself on more data.

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
