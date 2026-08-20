"""Swing Quantum — a floorsheet-only quantitative swing engine.

Implements ``swing_floorsheet_quantam.md``: turn every executed transaction into
quantitative features, aggregate them over 3D/7D/15D/30D, find recurring
structures, derive price zones, and test those zones historically.

What it may and may not claim is not decoration — it is the spec (sections 2,
107, 108). The floorsheet records *brokers*, not investors. "Broker 58 had net
buying" is a fact; "Broker 58 accumulated" is an inference; "Broker 58 is smart
money" is not sayable from this data at all. Every output here keeps its reasons
attached so a score is never a black box.

Layout::

    loader      sections 1-4     reading + data quality
    features    sections 5-10    price, volume, turnover, VWAP, volume-at-price
    brokers     sections 11-16   buy/sell, net flow, normalisation, imbalance
    flow        sections 17-21   accumulation, distribution, persistence
    structure   sections 22-32   breadth, concentration, trade structure
    network     sections 33-49   participation, rotation, pairs, network, anomaly
    history     sections 50-73   baselines, z-scores, price-flow, regime
    timeframes  sections 74-81   3/7/15/30D alignment, conflict, freshness
    zones       sections 82-92   entry/profit/invalidation zones and scores
    backtest    sections 93-104  labels, walk-forward, expectancy
    __main__                     build the board into Master_data/swing_quantam/
"""

from .loader import OUT, WINDOWS, Bar, Quality, Session, Trade, bars, load, load_last, sessions, symbols

__all__ = [
    "OUT",
    "WINDOWS",
    "Bar",
    "Quality",
    "Session",
    "Trade",
    "bars",
    "load",
    "load_last",
    "sessions",
    "symbols",
]
