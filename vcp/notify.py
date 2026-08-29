"""Telegram delivery for the daily scan.

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from the environment (locally or
GitHub Actions secrets). Message is HTML-formatted with a TradingView link per
symbol. Telegram caps messages at 4096 chars, so long lists are truncated.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

import pandas as pd

MAX_ROWS = 15


def tradingview_url(symbol: str) -> str:
    return f"https://www.tradingview.com/symbols/{urllib.parse.quote(symbol, safe='')}/"


def format_scan_message(result: pd.DataFrame, asof: str, market_on: bool,
                        equity: float) -> str:
    regime = "ON ✅" if market_on else "OFF ⛔ (no new entries per strategy)"
    head = (f"\U0001f4c8 <b>VCP Scan {asof}</b>\n"
            f"Market regime: {regime}\n"
            f"Sizing for equity ${equity:,.0f}\n")
    if result.empty:
        return head + "\nNo actionable candidates today."
    lines = [head, f"{len(result)} candidates (BUY STOP at trigger):\n"]
    for i, r in result.head(MAX_ROWS).iterrows():
        lines.append(
            f"{len(lines) - 1}. <a href=\"{tradingview_url(r.symbol)}\">"
            f"<b>{r.symbol}</b></a> — close {r.close:.2f}\n"
            f"   ▶ trigger <b>{r.trigger:.2f}</b> (+{r.dist_to_trigger_pct:.1f}%)"
            f" | stop {r.stop:.2f} (-{r.stop_pct:.1f}%)"
            f" | {int(r.shares)} sh ≈ ${r.position_value:,.0f}\n"
            f"   {int(r.n_contractions)} contractions, final {r.final_depth_pct:.1f}%,"
            f" VDU {r.vdu_ratio:.2f}, RS {r.rs_pct:.0f}\n")
    if len(result) > MAX_ROWS:
        lines.append(f"… and {len(result) - MAX_ROWS} more (see CSV).")
    lines.append("\nRules: skip fills &gt;5% above pivot; require breakout volume "
                 "≥ 1.4× 50d avg. Research tool, not investment advice.")
    return "\n".join(lines)


def send_telegram(text: str, token: str | None = None,
                  chat_id: str | None = None) -> dict:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError(
            "Telegram credentials missing: set TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID environment variables (see README).")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=payload)
    resp = json.load(urllib.request.urlopen(req, timeout=30))
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram API error: {resp}")
    return resp
