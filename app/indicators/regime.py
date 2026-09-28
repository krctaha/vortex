"""Piyasa rejimi siniflandirmasi - sinyal degil FILTRE."""
from __future__ import annotations

import numpy as np

from .core import adx, atr, choppiness, efficiency_ratio, ema

Array = np.ndarray


def classify(high: Array, low: Array, close: Array) -> dict:
    n = len(close)
    if n < 60:
        return {"regime": "bilinmiyor", "confidence": 0.0}
    er = efficiency_ratio(close, 20)
    adx_v, plus_di, minus_di = adx(high, low, close, 14)
    chop = choppiness(high, low, close, 14)
    a = atr(high, low, close, 14)
    e50 = ema(close, 50)
    e200 = ema(close, min(200, n - 1))

    er_v = float(er[-1]) if not np.isnan(er[-1]) else 0.0
    adx_last = float(adx_v[-1]) if not np.isnan(adx_v[-1]) else 0.0
    chop_last = float(chop[-1]) if not np.isnan(chop[-1]) else 50.0
    atr_pct = float(a[-1]) / float(close[-1]) * 100.0 if not np.isnan(a[-1]) else 0.0

    trend_votes = 0
    if er_v >= 0.30:
        trend_votes += 1
    if adx_last >= 22:
        trend_votes += 1
    if chop_last <= 45:
        trend_votes += 1

    if trend_votes >= 2:
        up = float(plus_di[-1]) > float(minus_di[-1]) if not np.isnan(plus_di[-1]) else close[-1] > e50[-1]
        regime = "trend_yukari" if up else "trend_asagi"
    elif chop_last >= 60 or er_v < 0.18:
        regime = "sikisik"
    else:
        regime = "gecis"

    atr_series = a[~np.isnan(a)]
    vol_rank = float(np.mean(atr_series[-1] >= atr_series[-min(200, len(atr_series)):])) if len(atr_series) else 0.5

    return {
        "regime": regime,
        "confidence": round(trend_votes / 3.0, 2),
        "efficiency_ratio": round(er_v, 3),
        "adx": round(adx_last, 2),
        "choppiness": round(chop_last, 2),
        "atr_pct": round(atr_pct, 3),
        "volatility_rank": round(vol_rank, 2),
        "above_ema50": bool(close[-1] > e50[-1]) if not np.isnan(e50[-1]) else None,
        "above_ema200": bool(close[-1] > e200[-1]) if not np.isnan(e200[-1]) else None,
    }


REGIME_LABEL = {
    "trend_yukari": "Yükseliş trendi",
    "trend_asagi": "Düşüş trendi",
    "sikisik": "Sıkışık / range",
    "gecis": "Geçiş",
    "bilinmiyor": "Yetersiz veri",
}
