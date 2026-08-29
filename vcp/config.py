"""Configuration for the VCP backtesting system.

All strategy parameters live here as nested dataclasses with Minervini-anchored
defaults (see docs/VCP_RESEARCH.md section 9). Configs can be overridden from
YAML files; unknown keys raise so typos never silently fall back to defaults.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class UniverseConfig:
    min_price: float = 5.0            # ignore sub-$5 stocks (Minervini avoids cheap stocks)
    min_dollar_volume: float = 2e6    # 20-day avg dollar volume floor at signal time
    min_history_days: int = 260       # need >1y of bars before a symbol is eligible


@dataclass
class TrendTemplateConfig:
    min_pct_above_52w_low: float = 0.30   # criterion 6
    max_pct_below_52w_high: float = 0.25  # criterion 7
    rs_percentile_min: float = 70.0       # criterion 8
    sma200_slope_days: int = 21           # criterion 3: SMA200 rising over this window


@dataclass
class VCPConfig:
    swing_window: int = 3             # bars on each side defining a swing high/low
    min_contractions: int = 2
    max_contractions: int = 6
    contraction_ratio_max: float = 0.75   # final depth <= ratio * first depth (envelope)
    noise_tolerance: float = 1.2          # depth[i] <= tol * depth[i-1] (local noise allowed)
    base_max_depth: float = 0.35          # deepest (first) contraction cap
    final_depth_max: float = 0.10         # last contraction tightness
    base_min_days: int = 25               # ~5 weeks
    base_max_days: int = 325              # ~65 weeks
    pivot_max_below_base_high: float = 0.10  # pivot must clear most overhead supply
    vdu_ratio_max: float = 0.85           # volume dry-up: final-leg vol vs 50d avg
    setup_max_active_days: int = 40       # setup expires if no breakout


@dataclass
class EntryConfig:
    breakout_buffer: float = 0.002    # trigger = pivot * (1 + buffer)
    max_chase_pct: float = 0.05       # skip fills further than this above pivot
    bo_vol_mult: float = 0.0          # breakout-day volume >= mult * 50d avg (0 = off)
    rank_by: str = "rs"               # candidate ranking: "rs" | "tightness"
    market_filter: bool = True        # only enter when SPY > its 200-day SMA
    market_filter_ma: int = 200
    bear_size_scale: float = 0.0      # 0 = no entries in bear regime; >0 = enter at this size fraction


@dataclass
class RiskConfig:
    stop_pct: float = 0.06            # initial stop distance cap (3-8% typical, <=10%)
    stop_use_contraction_low: bool = True  # tighter of stop_pct / final contraction low
    stop_max_pct: float = 0.10        # absolute never-exceed
    risk_per_trade: float = 0.01      # fraction of equity risked per trade
    max_positions: int = 8
    max_weight: float = 0.20          # per-position cap as fraction of equity


@dataclass
class ExitConfig:
    target_R: float = 3.0             # sell into strength at this R-multiple (0 = off)
    breakeven_at_R: float = 1.0       # raise stop to entry after this gain (0 = off)
    trail_ma: int = 50                # exit on close below this SMA (0 = off)
    trail_activation_R: float = 0.0   # arm the trail only after this gain (0 = always on)
    time_stop_days: int = 0           # exit stagnant trades after N days (0 = off)


@dataclass
class CostConfig:
    slippage_bps: float = 10.0        # per side
    commission_bps: float = 2.0       # per side


@dataclass
class BacktestConfig:
    start: str = "2004-01-01"
    end: str = "2026-08-01"
    initial_capital: float = 100_000.0


@dataclass
class Config:
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    tt: TrendTemplateConfig = field(default_factory=TrendTemplateConfig)
    vcp: VCPConfig = field(default_factory=VCPConfig)
    entry: EntryConfig = field(default_factory=EntryConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    exit: ExitConfig = field(default_factory=ExitConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    name: str = "base"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _apply_overrides(obj: Any, overrides: dict[str, Any], path: str = "") -> None:
    for key, value in overrides.items():
        if not hasattr(obj, key):
            raise KeyError(f"Unknown config key: {path}{key}")
        current = getattr(obj, key)
        if dataclasses.is_dataclass(current) and isinstance(value, dict):
            _apply_overrides(current, value, path=f"{path}{key}.")
        else:
            setattr(obj, key, type(current)(value) if current is not None else value)


def load_config(path: str | Path | None = None,
                overrides: dict[str, Any] | None = None) -> Config:
    """Build a Config from defaults, an optional YAML file, then dict overrides."""
    cfg = Config()
    if path is not None:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        _apply_overrides(cfg, data)
    if overrides:
        _apply_overrides(cfg, overrides)
    return cfg
