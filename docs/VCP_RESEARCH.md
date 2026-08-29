# Mark Minervini's VCP Strategy — Research & Quantified Rule Set

This document is the research foundation for the backtesting system in this repository.
It distills Mark Minervini's SEPA® methodology and the Volatility Contraction Pattern
(VCP) into an explicit, quantified rule set that code can implement, and augments it
with classical support/resistance principles. Sources: Minervini's books
*Trade Like a Stock Market Wizard* (2013), *Think & Trade Like a Champion* (2017),
his U.S. Investing Championship track record, and the broader
O'Neil/CANSLIM lineage from which SEPA descends.

---

## 1. Who is Minervini and what is SEPA

Mark Minervini is a U.S. stock trader, U.S. Investing Championship winner (1997, +155%;
again 2021, +334%), featured in Jack Schwager's *Stock Market Wizards*. His methodology,
SEPA (Specific Entry Point Analysis), aims to buy leading growth stocks at low-risk
entry points as they emerge from consolidation bases into new advances, and to control
risk ruthlessly so that average losses are a fraction of average gains.

SEPA has five key elements:

1. **Trend** — only buy stocks in confirmed Stage-2 uptrends (Weinstein stage analysis).
2. **Fundamentals** — earnings/sales acceleration (not available in our price-only dataset;
   we substitute stronger price/RS filters, which Minervini himself calls the leading proxy).
3. **Catalyst** — a reason institutions accumulate (again proxied by price/volume behavior).
4. **Entry point** — the VCP pivot: the precise low-risk entry.
5. **Exit point** — predefined stop-loss and profit-taking rules.

## 2. The Trend Template (Stage-2 filter)

Minervini's non-negotiable screen. A stock must meet ALL criteria before a setup
is even considered ("first the trend, then the setup"):

| # | Criterion | Quantified rule |
|---|-----------|-----------------|
| 1 | Price above both 150-day and 200-day SMA | `close > SMA150` and `close > SMA200` |
| 2 | 150-day SMA above 200-day SMA | `SMA150 > SMA200` |
| 3 | 200-day SMA trending up for at least 1 month | `SMA200 > SMA200[21 days ago]` (stricter: 4-5 months) |
| 4 | 50-day SMA above 150-day and 200-day SMA | `SMA50 > SMA150 > SMA200` |
| 5 | Price above 50-day SMA | `close > SMA50` |
| 6 | Price at least 30% above 52-week low | `close >= 1.30 * low_52w` (leaders are often 100%+) |
| 7 | Price within 25% of 52-week high | `close >= 0.75 * high_52w` |
| 8 | Relative Strength ranking ≥ 70 (IBD-style), preferably 80-90+ | percentile of weighted 3/6/9/12-month return vs. universe |

Our RS proxy (price-only, IBD-like): `RS_raw = 2*ret_63d + ret_126d + ret_189d + ret_252d`,
ranked cross-sectionally into percentiles each day.

## 3. The VCP — anatomy of the pattern

The VCP is Minervini's signature base pattern. After a strong Stage-2 advance, a stock
digests gains in a series of progressively *tighter* pullbacks — supply is absorbed
until it dries up, and the stock can rally through the pivot on light supply.

Structural properties:

- **Contractions (T1..Tn):** 2–6 successive pullbacks within the base (typically 2–4).
  Measured high-to-low, **each contraction is roughly half (or less) the depth of the
  previous one** (e.g., 25% → 12% → 6% → 3%). We quantify "tighter" as
  `depth[i] <= contraction_ratio_max * depth[i-1]` with `contraction_ratio_max ≈ 0.5–0.75`.
- **Overall base depth:** first (deepest) correction usually 10–35%; more than ~50%
  is failure-prone ("deep correction patterns fail"). In bear markets bases run deeper.
- **Base length:** typically 3–65 weeks; we require a minimum ~5 weeks (25 bars) and cap
  around 65 weeks.
- **Final contraction tightness:** the last contraction should be tight —
  ≤ ~10% deep, ideally 3–6%; day-to-day closing ranges narrow.
- **Volume dry-up (VDU):** volume contracts markedly in the final portion of the base —
  down days on light volume; 50-day volume MA declining across the base; final-leg volume
  well below the base average (we quantify: volume in last contraction ≤ ~70-85% of
  50-day average).
- **Pivot (buy point):** the high of the final contraction area (often just under the
  base high, or the handle high). This is the *line of least resistance*; NOT necessarily
  the 52-week high.
- **Breakout confirmation volume:** on the breakout day volume should expand
  (≥ ~1.3–2× the 50-day average is desirable). Intraday traders buy as the pivot is
  crossed; a daily-bar system buys when price crosses the pivot with a buy-stop.

Failure signs (avoid): widening contractions (W-shaped loosening), breakout on dry
volume with immediate churn, 3rd/4th-stage bases late in a long advance, broken
long-term trend during the base.

## 4. Entry rules (quantified)

1. Stock passes the Trend Template (all criteria) on the signal day.
2. A valid VCP is detected as of the signal day: `num_contractions >= 2`, depths
   contracting per §3, base depth/length within bounds, final tightness within bound,
   volume dry-up satisfied.
3. **Pivot** = high of final contraction. Entry trigger: price trades above
   `pivot * (1 + breakout_buffer)` (buffer ≈ 0.1–0.5% to avoid dead-cat ticks).
   In a daily backtest: if day's high crosses the trigger, fill at
   `max(open, trigger)` plus slippage (buy-stop semantics).
4. **Extension guard:** don't chase — if the fill would be > ~5% above the pivot, skip
   (Minervini: buy as close to the pivot as possible; "5% chase rule").
5. **Volume confirmation** (configurable): require breakout-day volume ≥ `bo_vol_mult`
   × 50-day average. NOTE: with daily bars this can only be *confirmed* after the close;
   using it to gate same-day entry is a mild look-ahead unless entry is deferred to the
   next day. We support both: `confirm_volume: none | same_day (approx) | next_day`.
6. **Market regime filter (M):** only take new entries when the general market is
   healthy: S&P 500 above its 200-day SMA (configurable; Minervini trades lighter/none
   in downtrends: "when it rains, everyone gets wet").
7. **Liquidity floor:** price ≥ $5 (avoid penny stocks; Minervini avoids cheap stocks)
   and 20-day average dollar-volume ≥ $2–5M so fills are realistic.

## 5. Risk management & position sizing

Minervini's core doctrine — the math of losses:

- **Initial stop-loss:** always predefined, placed under the pivot/low of the final
  contraction; **never risk more than 10%**, normal range **3–8%** below entry.
  Rule: `stop = entry * (1 - stop_pct)` with `stop_pct` ≈ 0.03–0.08, OR the low of
  the final contraction, whichever is *tighter*, floored at max 10%.
- **Position sizing:** risk-based. Risk per trade = `risk_per_trade` (0.5–1.25% of
  equity; Minervini: never > 2.5%). Size = `equity * risk_per_trade / stop_distance`.
  **Max position weight 20–25%** of equity (he runs 4–5 concentrated positions when
  hot, smaller when cold). No leverage in our base config.
- **Progressive exposure:** scale up only after recent trades work. (Implemented
  optionally via the market filter + reduced size after portfolio drawdown.)

## 6. Exit rules

1. **Hard stop:** exit when price hits the initial stop (gap-through fills at open).
2. **Break-even move:** once the trade gains ≥ ~1× initial risk, raise stop to entry
   (configurable; "never let a decent gain turn into a loss").
3. **Sell into strength / profit target:** take (part or all) profit at
   `target_R` × initial risk (Minervini's bread-and-butter: sell when the gain is a
   multiple of risk, typically 2–3R, or ~10–20% swing moves out of bases).
4. **Trailing exit for the remainder / alternative:** close below the 20-day or 50-day
   SMA (configurable `trail_ma`), or a trailing % stop.
5. **Time stop (squat rule):** if after `time_stop_days` (e.g., 20–40) the trade is
   below +1R and stalls, free the capital (configurable, off by default).
6. **Market-based tightening:** when the market filter turns off, tighten trailing
   stops (optional).

Expected profile: win rate ~35–50%, average win ≥ 2× average loss — the edge comes
from asymmetry, not hit-rate.

## 7. Support & resistance integration

Classical S/R principles woven into the system (per the user's requirement):

- **Pivot as resistance:** the VCP pivot *is* a resistance line formed by trapped
  supply; buying its clearance is buying the breakout of resistance → support flip.
- **Contraction lows as support:** each higher contraction low is a rising support
  shelf; the final contraction low is the natural stop location (that's why the stop
  belongs under it — if support fails the pattern failed).
- **Volume-at-price:** the dry-up requirement ensures little supply overhead near the
  pivot (thin volume shelf → little resistance above).
- **Prior-high resistance:** we optionally require the pivot to be within X% of the
  base high so the breakout clears the *entire* overhead supply zone, and (optional)
  the 52-week-high proximity from the trend template guarantees limited overhead
  resistance from older bag-holders.
- **Moving averages as dynamic S/R:** the 50-day SMA (institutional support) trailing
  exit uses this; trend-template alignment ensures the entry occurs above all major
  dynamic support layers.

## 8. Backtesting realism rules (no self-deception)

- **Next-bar semantics:** signals computed on bar T close; orders act on bar T+1
  (buy-stop at pivot trigger, evaluated against T+1's OHLC). No look-ahead.
- **Gap handling:** stops/limits that gap through fill at the open, not at the level.
- **Costs:** commission (default $0.005/share min $1, or 5 bps) + slippage
  (default 10 bps per side) on every fill.
- **Adjusted prices:** signals & fills computed on split/dividend-adjusted series
  (`adj_close`-factor applied to OHLC) for continuity; dollar-volume liquidity uses
  unadjusted close×volume.
- **Survivorship bias:** the dataset contains 7,700+ US symbols including many that
  stopped trading (delisted) — their history stays in the universe until their last
  bar. Positions in a symbol that stops trading are force-closed at the last close
  (a mild optimism vs. bankruptcy reality; noted in results).
- **In-sample / out-of-sample:** optimization iterations run on the IS window
  (e.g., 2004–2016); the chosen config is then validated untouched on OOS
  (e.g., 2017–2026). Both are reported. A config that only wins IS is overfit and
  rejected.
- **Benchmark:** buy-and-hold SPY total-return (adjusted close) over the identical
  window, same starting capital.

## 9. Parameter map (what the config controls)

| Config key | Meaning | Minervini anchor | Default |
|------------|---------|------------------|---------|
| `tt.min_pct_above_52w_low` | Criterion 6 | 30% | 0.30 |
| `tt.max_pct_below_52w_high` | Criterion 7 | 25% | 0.25 |
| `tt.rs_percentile_min` | Criterion 8 | 70 (pref. 90) | 70 |
| `tt.sma200_slope_days` | Criterion 3 lookback | 1 month | 21 |
| `vcp.base_max_depth` | First correction cap | 35% (50% hard) | 0.35 |
| `vcp.min_contractions` | Number of T's | 2–6 | 2 |
| `vcp.contraction_ratio_max` | Tightening envelope: final depth ≤ ratio × first depth | ~0.5 | 0.75 |
| `vcp.noise_tolerance` | Local noise allowed between consecutive contractions | — | 1.2 |
| `vcp.final_depth_max` | Last-leg tightness | 10% | 0.10 |
| `vcp.base_min_days` / `base_max_days` | Base length | 3–65 weeks | 25 / 325 |
| `vcp.vdu_ratio_max` | Final-leg vol vs 50d avg | "dry up" | 0.85 |
| `entry.breakout_buffer` | Above-pivot trigger | — | 0.002 |
| `entry.max_chase_pct` | Extension guard | ~5% | 0.05 |
| `entry.bo_vol_mult` | Breakout volume | 1.3–2× | off/1.3 |
| `entry.market_filter` | SPY vs 200SMA | M of SEPA | on |
| `risk.stop_pct` | Initial stop | 3–8% (≤10) | 0.06 |
| `risk.risk_per_trade` | Equity risk/trade | 0.5–1.25% | 0.01 |
| `risk.max_positions` | Concentration | 4–10 | 8 |
| `risk.max_weight` | Per-position cap | 20–25% | 0.20 |
| `exit.target_R` | Sell-into-strength | 2–3R | 3.0 |
| `exit.breakeven_at_R` | Stop→entry | ~1R | 1.0 |
| `exit.trail_ma` | Dynamic S/R exit | 20/50-day | 50 |

## 10. Known gaps vs. "true" Minervini

Honest limitations of a price-only daily-bar replication:

1. No fundamentals (EPS acceleration, margins) → we lean harder on RS and trend.
2. No intraday tape → pivot fills modeled with buy-stops on daily OHLC.
3. No discretionary pattern judgment → the detector is necessarily mechanical;
   Minervini rejects many "textbook" patterns on context we can't see.
4. Delisting returns are approximated (force-close at last bar).

These gaps mean results are an *approximation of the style*, not of the man.
