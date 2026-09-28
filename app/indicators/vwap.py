"""VWAP / Anchored VWAP + standart sapma bantlari."""
from __future__ import annotations

import numpy as np

Array = np.ndarray


def _typical(high: Array, low: Array, close: Array) -> Array:
    return (high + low + close) / 3.0


def anchored_vwap(high: Array, low: Array, close: Array, volume: Array,
                  anchor: int = 0, devs=(1.0, 2.0)):
    """anchor indeksinden itibaren kumulatif VWAP + sapma bantlari.
    Return: dict(vwap, upper_1, lower_1, upper_2, lower_2)"""
    n = len(close)
    tp = _typical(high, low, close)
    vwap = np.full(n, np.nan)
    band = {f"upper_{i+1}": np.full(n, np.nan) for i in range(len(devs))}
    band.update({f"lower_{i+1}": np.full(n, np.nan) for i in range(len(devs))})
    cum_pv = cum_v = cum_pv2 = 0.0
    for i in range(anchor, n):
        v = float(volume[i])
        p = float(tp[i])
        cum_pv += p * v
        cum_pv2 += p * p * v
        cum_v += v
        if cum_v <= 0:
            continue
        vw = cum_pv / cum_v
        vwap[i] = vw
        var = max(cum_pv2 / cum_v - vw * vw, 0.0)
        sd = var ** 0.5
        for k, d in enumerate(devs, start=1):
            band[f"upper_{k}"][i] = vw + d * sd
            band[f"lower_{k}"][i] = vw - d * sd
    out = {"vwap": vwap}
    out.update(band)
    return out


def session_vwap(high: Array, low: Array, close: Array, volume: Array,
                 open_time_ms: Array, session_hours: int = 24):
    """Her yeni UTC gunu (veya session_hours) basinda sifirlanan VWAP."""
    n = len(close)
    tp = _typical(high, low, close)
    vwap = np.full(n, np.nan)
    bucket_ms = session_hours * 3600_000
    cum_pv = cum_v = 0.0
    last_bucket = None
    for i in range(n):
        b = int(open_time_ms[i]) // bucket_ms
        if b != last_bucket:
            cum_pv = cum_v = 0.0
            last_bucket = b
        cum_pv += float(tp[i]) * float(volume[i])
        cum_v += float(volume[i])
        if cum_v > 0:
            vwap[i] = cum_pv / cum_v
    return vwap
