# Live Trading on IBKR Gateway — Setup & Operations

Turns the backtested Micro Pullback system into an automated live trader against
**IB Gateway**, with weekly self-maintenance that can auto-disable trading when
the edge degrades.

## Architecture

```
09:20 ET  cron/systemd starts live.run_live
          connect to Gateway (retry loop) · check trading_enabled flag · log equity
10:00 ET  morning scanner (live/scanner_live.py), two stages
          IBKR native scanner (top % gainers + most active, price/volume filtered)
          -> ONE market-data snapshot per chunk gives open / last / prev close,
             i.e. gap and early move for every candidate at no historical cost
          -> those names are ranked by an estimated score: gap, early move
             and today's volume vs its 90-day average (generic tick 165),
             scaled by elapsed session -- a stand-in for relative volume that
             costs nothing, used ONLY to choose who gets examined
          -> the top `scanner_deep_max` (default 12) get their 1m history
             pulled; eligibility and the published score always use the real
             relative volume computed from those bars
          -> score by gap / early move / early relative volume (same formula that
             was validated in backtest) -> top-K watchlist (default 3)
10:01+    subscribe streaming 1m bars per watchlist symbol
          every completed bar -> engine/strategy.scan_signals on the rolling window
          fresh setup -> BUY-STOP @ prev high + $0.01 with OCA bracket children
          (take-profit limit @ 3R, protective stop under pullback low, capped 1.5%)
          untriggered entries cancelled after 2 bars
15:30 ET  no new entries
15:55 ET  cancel all orders, flatten all positions (plus a 15:58 cron safety net)
16:05 ET  daily summary (Telegram + SQLite) and shutdown
```

**Risk guards before every order:** `trading_enabled` flag (maintenance-controlled),
daily kill-switch (flatten + stop at −3% day PnL), max 3 concurrent positions,
max 12 entries/day, risk 1.5% equity per trade, notional ≤ 2.5× equity / slots.

## One-time setup

1. **IB Gateway** (or TWS) on the same machine:
   - Configure → API → Settings: *Enable ActiveX and Socket Clients*, port
     **4002** (paper) / **4001** (live), trusted IP 127.0.0.1,
     *Read-Only API OFF*.
   - For unattended running install **IBC** (IBKR's auto-login/restarter) or use
     the `ib-gateway-docker` image; the Gateway restarts nightly by design.
   - Market data subscriptions: US equities Level 1 (NYSE/NASDAQ) on the account.
2. **Project**:
   ```bash
   cd /opt && git clone <repo> IntraDayTradingAlgo && cd IntraDayTradingAlgo
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   .venv/bin/python -m live.run_live --status   # creates config.live.json template
   ```
3. **Edit `config.live.json`** — port (4002 first!), risk, scanner thresholds, and
   optionally `telegram_bot_token` / `telegram_chat_id` for push alerts.
4. **Schedule** — either cron (`crontab deploy/crontab.txt` after fixing paths) or
   systemd (`sudo cp deploy/systemd/* /etc/systemd/system/ && sudo systemctl
   enable --now trading-live.timer trading-maintenance.timer`).

## Operations

| Command | Purpose |
|---|---|
| `python -m live.monitor --ib` | dashboard plus live prices, positions and a per-symbol **"needs for entry"** column (own clientId, reads only) |
| `python -m live.monitor` | **read-only console dashboard** — phase clock, scanner picks, orders, fills, live log tail. Safe to run alongside a session (opens no IB connection) |
| `python -m live.run_live --status` | connectivity, equity, open positions, flags, live stats |
| `python -m live.run_live --scan-only` | dry-run the 10:00 scanner |
| `python -m live.run_live --flatten` | emergency: cancel everything, market-out all positions |
| `python -m maintenance.run_maintenance` | run full maintenance now |
| `python -m maintenance.run_maintenance --enable` / `--disable` | manual trading switch |

State lives in `state/live.db` (SQLite: trades, orders, daily PnL, scanner picks,
flags) and `state/logs/` (rotating). Maintenance reports land in `reports/`.

## Automated weekly maintenance (Sunday 12:00 ET)

1. Rebuilds the volume-filtered universe (S&P 500 + Nasdaq-100 + extras).
2. Clears data caches and re-downloads fresh Yahoo intraday history.
3. Re-runs the **strict walk-forward** evaluation on fresh data.
4. Re-runs the **scanner sweep** on the fresh 1m window.
5. **Drift check**: live PF from the SQLite trade log (once ≥ 20 live trades).
6. **Health verdict**: walk-forward PF ≥ 1.0 and live PF ≥ 0.9 required —
   otherwise `trading_enabled` is switched **off automatically** and the engine
   stands down until you re-enable (`--enable`). Fails safe.
7. Dated markdown report + Telegram summary.

## Go-live checklist

- [ ] 4–8 weeks on **paper** (port 4002) minimum
- [ ] Compare paper fills vs backtest assumptions: entry slippage on buy-stops,
      6 bps round-trip cost model, win rate in `state/live.db` vs backtest
- [ ] Only then switch port to 4001 with small size (`risk_per_trade_pct: 0.5`)
- [ ] Account ≥ $25k (US pattern-day-trader rule) or trade ≤ 3 day-trades/week
- [ ] Kill-switch and `--flatten` tested on paper at least once

## Known gaps vs the backtest

- Live entry anticipates the breakout (stop order at prev high + 1c) while the
  backtest samples it at bar level — expect small fill differences; measure them.
- Scanner candidates come from IBKR's screener (real-time, survivorship-free),
  while the backtest scanned a fixed 150-name universe — live coverage is wider.
- Yahoo data feeds the *research* loop only; all live decisions use IBKR data.
- IBKR allows only ~60 historical-data requests per ten minutes. The candidate
  list routinely exceeds 60 names, so the scanner must not pull history per
  candidate -- hence the snapshot screen above. If you raise `scanner_deep_max`,
  keep the deep stage well under that budget or the scan will be throttled to a
  crawl. A timed-out historical request returns an EMPTY list rather than an
  error, so anything derived from it must drop the symbol, never substitute a
  default.
