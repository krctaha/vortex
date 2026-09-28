"""Hacim agirlikli cok-zaman-ufuklu likidite seviyeleri ve sweep sinyalleri.

TradingView/Pine calisma zamanina bagli olmayan, VORTEX icin bagimsiz Python
uyarlamasidir. 14/42/120 bar ekstrem seviyelerini izler; ayni mumda en az iki
ayri bandin alinip iki bar icinde geri kazanilmasini sweep kabul eder.
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

Array = np.ndarray


def _rolling_mean(values: Array, period: int) -> Array:
    out = np.full(len(values), np.nan)
    for i in range(period - 1, len(values)):
        out[i] = float(np.mean(values[i-period+1:i+1]))
    return out


def detect(open_: Array, high: Array, low: Array, close: Array, volume: Array,
           lookbacks=(14, 42, 120), reclaim_bars: int = 2,
           min_levels: int = 2) -> Dict[str, Any]:
    n = len(close)
    if n < max(lookbacks) + 5:
        return {"upper_levels": [], "lower_levels": [], "signals": []}

    avg_vol = _rolling_mean(volume.astype(float), 100)
    levels: List[Dict[str, Any]] = []
    tolerance = max(float(close[-1]) * 0.00008, 1e-12)

    def add_level(price: float, side: str, index: int) -> None:
        for lvl in levels:
            if lvl["side"] == side and abs(lvl["price"] - price) <= tolerance:
                lvl["last_seen"] = index
                lvl["touches"] += 1
                return
        levels.append({"price": float(price), "side": side, "created_index": index,
                       "last_seen": index, "touches": 1, "strength": 0.0,
                       "active": True})

    signals: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []

    for i in range(max(lookbacks), n):
        # Once mevcut seviyelerin sweep/mitigasyonunu isle; sonra yeni ekstremi ekle.
        uppers = [x for x in levels if x["active"] and x["side"] == "upper" and
                  x["created_index"] < i and high[i] >= x["price"]]
        lowers = [x for x in levels if x["active"] and x["side"] == "lower" and
                  x["created_index"] < i and low[i] <= x["price"]]
        if len(uppers) >= min_levels:
            pending.append({"index": i, "direction": "bear", "reclaim": min(x["price"] for x in uppers),
                            "levels": [x["price"] for x in uppers],
                            "strength": float(np.mean([x["strength"] for x in uppers]))})
        if len(lowers) >= min_levels:
            pending.append({"index": i, "direction": "bull", "reclaim": max(x["price"] for x in lowers),
                            "levels": [x["price"] for x in lowers],
                            "strength": float(np.mean([x["strength"] for x in lowers]))})

        keep = []
        for p in pending:
            age = i - p["index"]
            reclaimed = (p["direction"] == "bear" and close[i] < p["reclaim"]) or \
                        (p["direction"] == "bull" and close[i] > p["reclaim"])
            if reclaimed:
                signals.append({"index": i, "sweep_index": p["index"],
                                "direction": p["direction"], "price": float(close[i]),
                                "reclaim_level": round(float(p["reclaim"]), 8),
                                "swept_levels": [round(float(x), 8) for x in p["levels"][:6]],
                                "band_count": len(p["levels"]),
                                "strength": round(min(100.0, p["strength"] * 2.0), 2)})
            elif age < reclaim_bars:
                keep.append(p)
        pending = keep

        # Wick gecisiyle eski bandi aktif listeden cikar.
        for lvl in uppers + lowers:
            lvl["active"] = False

        # Yonlu normalize hacim, fiyata en yakin aktif 3 banda akar.
        denom = avg_vol[i] if np.isfinite(avg_vol[i]) and avg_vol[i] > 0 else volume[i]
        normalized = min(float(volume[i] / denom), 3.0) if denom > 0 else 0.0
        target_side = "lower" if close[i] > open_[i] else "upper" if close[i] < open_[i] else None
        if target_side:
            active = [x for x in levels if x["active"] and x["side"] == target_side]
            active.sort(key=lambda x: abs(x["price"] - close[i]))
            for weight, lvl in zip((1.0, 0.25, 0.05), active[:3]):
                lvl["strength"] = min(50.0, lvl["strength"] + normalized * weight)

        for period in lookbacks:
            hs = high[i-period+1:i+1]
            ls = low[i-period+1:i+1]
            if high[i] >= float(np.max(hs)) and high[i] > high[i-3]:
                add_level(float(high[i]), "upper", i)
            if low[i] <= float(np.min(ls)) and low[i] < low[i-3]:
                add_level(float(low[i]), "lower", i)

    price = float(close[-1])
    upper = [x for x in levels if x["active"] and x["price"] > price]
    lower = [x for x in levels if x["active"] and x["price"] < price]
    upper.sort(key=lambda x: x["price"])
    lower.sort(key=lambda x: x["price"], reverse=True)

    def clean(items):
        max_strength = max((x["strength"] for x in items), default=1.0) or 1.0
        return [{"price": round(x["price"], 8), "strength": round(x["strength"] / max_strength * 100, 2),
                 "touches": x["touches"], "created_index": x["created_index"],
                 "side": x["side"]} for x in items[:3]]

    return {"upper_levels": clean(upper), "lower_levels": clean(lower),
            "signals": signals[-20:], "lookbacks": list(lookbacks),
            "reclaim_bars": reclaim_bars, "min_levels": min_levels}
