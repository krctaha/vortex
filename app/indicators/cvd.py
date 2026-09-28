"""Cumulative Volume Delta - kline'daki takerBuyBaseVolume'dan tam olcum."""
from __future__ import annotations

import numpy as np

Array = np.ndarray


def delta(volume: Array, taker_buy_base: Array) -> Array:
    """Bar bazinda net agresyon: alici - satici."""
    return 2.0 * taker_buy_base.astype(float) - volume.astype(float)


def cvd(volume: Array, taker_buy_base: Array) -> Array:
    return np.cumsum(delta(volume, taker_buy_base))


def cvd_divergence(close: Array, cvd_series: Array, lookback: int = 20):
    """Fiyat yeni tepe yaparken CVD yapmiyorsa (veya tersi) isaretle.
    Return: 'bearish' | 'bullish' | None"""
    if len(close) < lookback * 2:
        return None
    a, b = close[-lookback * 2:-lookback], close[-lookback:]
    ca, cb = cvd_series[-lookback * 2:-lookback], cvd_series[-lookback:]
    if float(np.max(b)) > float(np.max(a)) and float(np.max(cb)) < float(np.max(ca)):
        return "bearish"
    if float(np.min(b)) < float(np.min(a)) and float(np.min(cb)) > float(np.min(ca)):
        return "bullish"
    return None
