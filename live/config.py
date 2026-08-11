"""Live trading configuration: config.live.json overrides the defaults below."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.live.json")
STATE_DIR = os.path.join(ROOT, "state")
REPORTS_DIR = os.path.join(ROOT, "reports")


@dataclass
class LiveConfig:
    # --- IB Gateway connection ---
    host: str = "127.0.0.1"
    port: int = 4002              # 4002 = Gateway paper, 4001 = Gateway live
    client_id: int = 17
    account: str = ""             # empty = first account on the connection

    # --- session schedule (ET) ---
    scan_time: str = "10:00"      # morning scanner runs at this time
    entry_end: str = "15:30"      # no new entries after this
    eod_flat: str = "15:55"       # flatten everything
    shutdown: str = "16:05"

    # --- scanner ---
    scanner_top_k: int = 3
    gap_min: float = 0.03         # 3% opening gap qualifies
    move_min: float = 0.02        # or 2% move from open ...
    early_rv_min: float = 2.0     # ... on 2x usual cumulative volume
    min_price: float = 5.0
    max_price: float = 2000.0
    min_day_volume: int = 2_000_000

    # --- strategy (the validated 1m production config) ---
    relvol_min: float = 1.7
    momentum_min_gain_atr: float = 1.5
    max_pullback_bars: int = 2
    target_rr: float = 3.0
    stop_cap_pct: float = 1.5
    require_macd: bool = True

    # --- risk ---
    risk_per_trade_pct: float = 1.5
    pos_leverage_cap: float = 2.5
    max_concurrent: int = 3
    daily_loss_limit_pct: float = 3.0   # kill-switch: stop for the day at -3%
    max_trades_per_day: int = 12
    entry_order_ttl_bars: int = 2       # cancel untriggered buy-stops after N bars

    # --- notifications (leave empty to disable) ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- maintenance ---
    maint_min_live_trades: int = 20     # need this many live trades before drift checks
    maint_live_pf_floor: float = 0.9    # auto-disable if live PF drops below this
    maint_wf_pf_floor: float = 1.0      # auto-disable if fresh walk-forward PF below this

    extra: dict = field(default_factory=dict)


def load_config() -> LiveConfig:
    cfg = LiveConfig()
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
            else:
                cfg.extra[k] = v
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    return cfg


def save_template() -> None:
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w") as f:
            json.dump({k: v for k, v in asdict(LiveConfig()).items() if k != "extra"},
                      f, indent=2)


def hhmm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)
