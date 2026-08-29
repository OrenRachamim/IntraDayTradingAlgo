import pandas as pd
import pytest

from vcp.notify import format_scan_message, send_telegram, tradingview_url


def _result():
    return pd.DataFrame([{
        "symbol": "ARWR", "close": 88.59, "trigger": 90.18,
        "dist_to_trigger_pct": 1.79, "stop": 85.50, "stop_pct": 5.19,
        "shares": 387, "position_value": 34900.0, "n_contractions": 4,
        "final_depth_pct": 5.0, "vdu_ratio": 0.51, "rs_pct": 93.4,
    }])


def test_tradingview_url():
    assert tradingview_url("ARWR") == "https://www.tradingview.com/symbols/ARWR/"
    assert "%2F" in tradingview_url("BRK/B")     # slash symbols stay URL-safe


def test_message_contains_link_trigger_and_stop():
    msg = format_scan_message(_result(), "2026-08-29", True, 100_000)
    assert "tradingview.com/symbols/ARWR/" in msg
    assert "90.18" in msg and "85.50" in msg
    assert "ON" in msg
    assert len(msg) < 4096


def test_empty_scan_message():
    msg = format_scan_message(_result().iloc[0:0], "2026-08-29", False, 100_000)
    assert "No actionable candidates" in msg
    assert "OFF" in msg


def test_send_requires_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(RuntimeError, match="credentials missing"):
        send_telegram("hi")
