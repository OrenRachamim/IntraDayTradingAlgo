"""Minervini VCP (Volatility Contraction Pattern) backtesting system.

Implements Mark Minervini's SEPA/VCP methodology on daily US stock data:
trend template screening, VCP detection, pivot breakout entries,
risk-based position sizing, and portfolio-level backtesting vs. S&P 500.

See docs/VCP_RESEARCH.md for the quantified rule set.
"""

__version__ = "0.1.0"
