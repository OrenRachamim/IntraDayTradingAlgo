#!/usr/bin/env python3
"""Entry point for the live engine.

  python -m live.run_live              # full trading session (per config)
  python -m live.run_live --scan-only  # run the 10:00 scanner and exit
  python -m live.run_live --flatten    # emergency: cancel all + flatten all
  python -m live.run_live --status     # connection + account + flags check
"""
from __future__ import annotations

import sys

from .broker import Broker
from .config import load_config, save_template
from .engine_live import LiveEngine
from .notify import notify
from . import state


def main() -> None:
    save_template()
    cfg = load_config()
    if "--status" in sys.argv:
        b = Broker(cfg)
        b.connect(retries=1)
        print(f"equity: ${b.equity():,.0f}")
        print(f"open positions: {b.open_position_symbols() or 'none'}")
        print(f"trading_enabled: {state.trading_enabled()}")
        print(f"live stats: {state.live_trade_stats()}")
        b.disconnect()
        return
    if "--flatten" in sys.argv:
        b = Broker(cfg)
        b.connect(retries=2)
        b.flatten_all(reason="manual")
        notify(cfg, "manual flatten executed", important=True)
        b.disconnect()
        return
    if "--scan-only" in sys.argv:
        from .scanner_live import run_scanner
        b = Broker(cfg)
        b.connect(retries=2)
        picks = run_scanner(b, cfg)
        for p in picks:
            print(p)
        b.disconnect()
        return
    LiveEngine(cfg).run()


if __name__ == "__main__":
    main()
