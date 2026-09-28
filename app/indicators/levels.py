"""Destek/direnc: pivot kumeleme + hacim profili (POC/VAH/VAL)."""
from __future__ import annotations

from typing import List

import numpy as np

from .structure import find_pivots

Array = np.ndarray


def pivot_levels(high: Array, low: Array, close: Array,
                 left: int = 3, right: int = 3,
                 tolerance: float = 0.0035,
                 max_levels: int = 10,
                 max_distance_pct: float = 15.0,
                 recency_decay: float = 2.6) -> List[dict]:
    """Yakin pivotlari kumeler; dokunus sayisi ve YENILIK guc verir.

    27.08.2026 DUZELTMESI — iki hata vardi:

    1) Agirlik formulu `touches * (0.55 + 0.45*yenilik)` idi. Yenilik carpani
       yalnizca 0.55-1.0 arasinda oynadigi icin 80 gun onceki 6 dokunuslu bir
       seviye (3.30), dunku 3 dokunuslu seviyeyi (3.00) yeniyordu. BTC 63bin'den
       79bin'e ciktiginda analiz hala 65bin'den bahsediyordu.
       Artik yenilik USTEL olarak sonumleniyor: en yeni seviye 1.0, en eski
       exp(-2.6) ~ 0.074 agirlik aliyor. Aradaki fark 13 kat.

    2) Mesafe filtresi yoktu ve "en guclu N" secilip fiyata gore ayriliyordu.
       Fiyat rallide tum kumelerin uzerine ciktiginda DIRENC LISTESI BOS
       kaliyordu. Artik destek ve direnc AYRI AYRI secilir, ve fiyattan
       max_distance_pct'ten uzak seviyeler islem plani icin anlamsiz sayilip
       elenir.
    """
    pivots = find_pivots(high, low, left, right)
    if not pivots:
        return []
    price_now = float(close[-1])
    n = len(close)

    raw = sorted(((p.price, p.index, p.kind) for p in pivots), key=lambda x: x[0])
    clusters: List[dict] = []
    for price, idx, kind in raw:
        if clusters and abs(price - clusters[-1]["price"]) / max(price, 1e-12) <= tolerance:
            cl = clusters[-1]
            cl["prices"].append(price)
            cl["indexes"].append(idx)
            cl["price"] = float(np.mean(cl["prices"]))
            cl["touches"] += 1
            cl["last_index"] = max(cl["last_index"], idx)
        else:
            clusters.append({
                "price": float(price), "prices": [price], "indexes": [idx],
                "touches": 1, "last_index": idx,
            })

    for cl in clusters:
        cl["kind"] = "resistance" if cl["price"] > price_now else "support"
        cl["distance_pct"] = round((cl["price"] - price_now) / price_now * 100.0, 3)
        age = (n - 1 - cl["last_index"]) / max(n - 1, 1)      # 0 = en yeni
        cl["recency"] = round(float(np.exp(-recency_decay * age)), 4)
        cl["strength"] = round(cl["touches"] * cl["recency"], 4)
        cl["price"] = round(cl["price"], 8)
        cl["bars_ago"] = n - 1 - cl["last_index"]
        cl.pop("prices", None)

    # Islem plani icin anlamli bantta kal.
    near = [c for c in clusters if abs(c["distance_pct"]) <= max_distance_pct]
    if not near:
        near = clusters

    # Destek ve direnci AYRI sec: biri digerini kalabalikla ezmesin.
    per_side = max(1, max_levels // 2)
    sup = sorted([c for c in near if c["kind"] == "support"],
                 key=lambda c: c["strength"], reverse=True)[:per_side]
    res = sorted([c for c in near if c["kind"] == "resistance"],
                 key=lambda c: c["strength"], reverse=True)[:per_side]
    out = sup + res
    out.sort(key=lambda c: c["price"])
    return out


def nearest_levels(levels: List[dict], price: float) -> dict:
    """En yakin destek/direnc. Hicbiri yoksa bunu ACIKCA bildirir.

    `no_resistance=True`, fiyatin bakilan pencerede yapisal bir engelin
    uzerinde oldugu anlamina gelir (kirilim / aralik tepesi). Cagiran taraf
    bunu "direnc yok" diye bos birakmak yerine boyle anlatmali; eskiden
    rapora "Dirençler: — / —" diye dusuyordu ve hicbir sey ifade etmiyordu.
    """
    sup = [l for l in levels if l["price"] < price]
    res = [l for l in levels if l["price"] > price]
    return {
        "support": max(sup, key=lambda l: l["price"]) if sup else None,
        "resistance": min(res, key=lambda l: l["price"]) if res else None,
        "no_resistance": not res,
        "no_support": not sup,
    }


def volume_profile(high: Array, low: Array, close: Array, volume: Array,
                   bins: int = 48, value_area: float = 0.70) -> dict:
    """POC / VAH / VAL. Her barin hacmi high-low araligina esit dagitilir."""
    lo, hi = float(np.min(low)), float(np.max(high))
    if hi <= lo:
        return {}
    edges = np.linspace(lo, hi, bins + 1)
    hist = np.zeros(bins)
    for i in range(len(close)):
        b_lo, b_hi, vol = float(low[i]), float(high[i]), float(volume[i])
        if vol <= 0:
            continue
        if b_hi <= b_lo:
            k = min(int((b_lo - lo) / (hi - lo) * bins), bins - 1)
            hist[k] += vol
            continue
        start = max(int((b_lo - lo) / (hi - lo) * bins), 0)
        end = min(int((b_hi - lo) / (hi - lo) * bins), bins - 1)
        share = vol / (end - start + 1)
        hist[start:end + 1] += share
    poc_idx = int(np.argmax(hist))
    total = float(np.sum(hist))
    target = total * value_area
    lo_i = hi_i = poc_idx
    acc = hist[poc_idx]
    while acc < target and (lo_i > 0 or hi_i < bins - 1):
        down = hist[lo_i - 1] if lo_i > 0 else -1.0
        up = hist[hi_i + 1] if hi_i < bins - 1 else -1.0
        if up >= down:
            hi_i += 1
            acc += hist[hi_i]
        else:
            lo_i -= 1
            acc += hist[lo_i]
    mid = (edges[:-1] + edges[1:]) / 2.0
    return {
        "poc": round(float(mid[poc_idx]), 8),
        "vah": round(float(edges[hi_i + 1]), 8),
        "val": round(float(edges[lo_i]), 8),
        "bins": [{"price": round(float(mid[i]), 8), "volume": round(float(hist[i]), 4)}
                 for i in range(bins)],
        "max_volume": round(float(np.max(hist)), 4),
    }
