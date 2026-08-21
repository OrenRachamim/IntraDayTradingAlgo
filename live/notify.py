"""Telegram notifications (no-op when unconfigured) + rotating file log."""
from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler

import requests

from .config import LiveConfig, STATE_DIR

_log = None


def get_logger() -> logging.Logger:
    global _log
    if _log is None:
        _log = logging.getLogger("live")
        _log.setLevel(logging.INFO)
        os.makedirs(os.path.join(STATE_DIR, "logs"), exist_ok=True)
        fh = TimedRotatingFileHandler(os.path.join(STATE_DIR, "logs", "live.log"),
                                      when="midnight", backupCount=30, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        sh = logging.StreamHandler()
        # Windows consoles default to a legacy codepage; keep a stray emoji
        # in a log line from raising inside logging.
        if hasattr(sh.stream, "reconfigure"):
            try:
                sh.stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):
                pass
        sh.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        _log.addHandler(fh)
        _log.addHandler(sh)
        # ib_async logs to its own logger with no handler attached, so its
        # warnings went to stderr and never survived the session. Give it the
        # same handlers; INFO would be far too chatty.
        ib_log = logging.getLogger("ib_async")
        ib_log.setLevel(logging.WARNING)
        ib_log.handlers = [fh, sh]
        ib_log.propagate = False
    return _log


def telegram_configured(cfg: LiveConfig) -> bool:
    return bool(cfg.telegram_bot_token and cfg.telegram_chat_id)


def send_telegram(cfg: LiveConfig, text: str) -> bool:
    """Push one Telegram message. False when unconfigured or the send failed.

    Separate from notify() so a caller that keeps its own record -- the
    dashboard, which must not write into the engine's operational log -- can
    push an alert without logging through the engine's logger.
    """
    if not telegram_configured(cfg):
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage",
            json={"chat_id": cfg.telegram_chat_id, "text": text}, timeout=10)
        # A bad token answers 401 and a bad chat_id 400, both without raising.
        # Reporting those as sent would leave the operator believing alerts are
        # going out while nothing ever arrives.
        body = r.json() if r.content else {}
        if r.ok and body.get("ok"):
            return True
        get_logger().warning(
            f"telegram rejected the message: HTTP {r.status_code} "
            f"{body.get('description') or r.reason}")
        return False
    except Exception as e:  # noqa: BLE001 - notifications must never kill the engine
        get_logger().warning(f"telegram send failed: {e}")
        return False


def notify(cfg: LiveConfig, text: str, important: bool = False) -> None:
    """Log always; push to Telegram when configured (always for important)."""
    get_logger().info(text)
    send_telegram(cfg, text)
