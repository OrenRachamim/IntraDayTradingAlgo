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
                                      when="midnight", backupCount=30)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        _log.addHandler(fh)
        _log.addHandler(sh)
    return _log


def notify(cfg: LiveConfig, text: str, important: bool = False) -> None:
    """Log always; push to Telegram when configured (always for important)."""
    get_logger().info(text)
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage",
            json={"chat_id": cfg.telegram_chat_id, "text": text}, timeout=10)
    except Exception as e:  # noqa: BLE001 - notifications must never kill the engine
        get_logger().warning(f"telegram send failed: {e}")
