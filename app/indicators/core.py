"""Klasik indikatörler. Hepsi numpy dizisi alır, numpy dizisi döner.
Uzunluk daima girdiyle aynıdır; hesaplanamayan baş kısım NaN'dir."""
from __future__ import annotations

import numpy as np

Array = np.ndarray


def _nan(n: int) -> Array:
    return np.full(n, np.nan, dtype=float)


def sma(values: Array, period: int) -> Array:
    n = len(values)
    out = _nan(n)
    if n < period or period < 1:
        return out
    csum = np.cumsum(np.insert(values.astype(float), 0, 0.0))
    out[period - 1:] = (csum[period:] - csum[:-period]) / period
    return out


def ema(values: Array, period: int) -> Array:
    n = len(values)
    out = _nan(n)
    if n < period or period < 1:
        return out
    k = 2.0 / (period + 1.0)
    out[period - 1] = float(np.mean(values[:period]))
    for i in range(period, n):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def rma(values: Array, period: int) -> Array:
    """Wilder yumusatma - RSI ve ATR'nin dogru versiyonu bunu kullanir."""
    n = len(values)
    out = _nan(n)
    if n < period or period < 1:
        return out
    out[period - 1] = float(np.mean(values[:period]))
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def rsi(close: Array, period: int = 14) -> Array:
    n = len(close)
    if n < period + 1:
        return _nan(n)
    close = close.astype(float)
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = rma(gain, period)
    avg_loss = rma(loss, period)
    out = _nan(n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.divide(avg_gain, avg_loss, out=np.full_like(avg_gain, np.inf), where=avg_loss != 0)
        vals = 100.0 - (100.0 / (1.0 + rs))
    vals[avg_loss == 0] = 100.0
    vals[(avg_loss == 0) & (avg_gain == 0)] = 50.0
    out[1:] = vals
    return out


def true_range(high: Array, low: Array, close: Array) -> Array:
    prev_close = np.roll(close.astype(float), 1)
    prev_close[0] = close[0]
    return np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low - prev_close),
    ])


def atr(high: Array, low: Array, close: Array, period: int = 14) -> Array:
    return rma(true_range(high, low, close), period)


def bollinger(close: Array, period: int = 20, mult: float = 2.0):
    basis = sma(close, period)
    n = len(close)
    dev = _nan(n)
    for i in range(period - 1, n):
        dev[i] = float(np.std(close[i - period + 1: i + 1]))
    return basis, basis + mult * dev, basis - mult * dev, dev


def bb_width(close: Array, period: int = 20, mult: float = 2.0) -> Array:
    basis, upper, lower, _ = bollinger(close, period, mult)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (upper - lower) / basis * 100.0


def adx(high: Array, low: Array, close: Array, period: int = 14):
    """Return (adx, plus_di, minus_di)."""
    n = len(close)
    if n < period * 2 + 2:
        return _nan(n), _nan(n), _nan(n)
    up = np.diff(high.astype(float), prepend=high[0])
    dn = -np.diff(low.astype(float), prepend=low[0])
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr_rma = rma(true_range(high, low, close), period)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * rma(plus_dm, period) / tr_rma
        minus_di = 100.0 * rma(minus_dm, period) / tr_rma
        dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    out = _nan(n)
    valid = np.where(np.isfinite(dx))[0]
    if len(valid) >= period:
        out[valid] = rma(dx[valid], period)
    return out, plus_di, minus_di


def supertrend(high: Array, low: Array, close: Array, period: int = 10, mult: float = 3.0):
    """Return (line, direction) - direction: +1 yukari trend, -1 asagi."""
    n = len(close)
    a = atr(high, low, close, period)
    hl2 = (high + low) / 2.0
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    line = _nan(n)
    direction = np.zeros(n, dtype=int)
    final_up, final_low = np.nan, np.nan
    for i in range(n):
        if np.isnan(a[i]):
            continue
        if np.isnan(final_up):
            final_up, final_low = upper[i], lower[i]
            direction[i] = 1
            line[i] = final_low
            continue
        final_up = upper[i] if (upper[i] < final_up or close[i - 1] > final_up) else final_up
        final_low = lower[i] if (lower[i] > final_low or close[i - 1] < final_low) else final_low
        prev = direction[i - 1] if direction[i - 1] != 0 else 1
        if prev == 1 and close[i] < final_low:
            direction[i] = -1
        elif prev == -1 and close[i] > final_up:
            direction[i] = 1
        else:
            direction[i] = prev
        line[i] = final_low if direction[i] == 1 else final_up
    return line, direction


def efficiency_ratio(close: Array, period: int = 20) -> Array:
    """Kaufman ER - 0 (tam gurultu) .. 1 (tam trend). Rejim filtresi."""
    n = len(close)
    out = _nan(n)
    if n <= period:
        return out
    absdiff = np.abs(np.diff(close.astype(float)))
    for i in range(period, n):
        direction = abs(float(close[i] - close[i - period]))
        volatility = float(np.sum(absdiff[i - period:i]))
        out[i] = direction / volatility if volatility > 0 else 0.0
    return out


def choppiness(high: Array, low: Array, close: Array, period: int = 14) -> Array:
    """>61.8 sikisik/range, <38.2 trend."""
    n = len(close)
    out = _nan(n)
    tr = true_range(high, low, close)
    for i in range(period, n):
        window_tr = float(np.sum(tr[i - period + 1: i + 1]))
        rng = float(np.max(high[i - period + 1: i + 1]) - np.min(low[i - period + 1: i + 1]))
        if rng > 0 and window_tr > 0:
            out[i] = 100.0 * np.log10(window_tr / rng) / np.log10(period)
    return out
