"""Araştırma işlemleri için brüt / maliyet / net R muhasebesi.

`result_r` eski sürümlerde yalnız fiyat hareketiydi. V4 bunu silmez; brüt
hareketi, tahmini gerçek işlem maliyetini ve net sonucu ayrı sütunlarda
tutar. Fonlama, girişteki ileri tahminin gerçekleşen tutma süresine oranlı
kısmıdır; kesin borsa ekstresi olmadığı için açıkça tahmin olarak kalır.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable

from .. import db

MS_DAY = 86_400_000
# İki market emir + iki yön slipaj için ihtiyatlı araştırma varsayımı.
FALLBACK_ROUNDTRIP_PCT = 2 * 0.00045 + 2 * 0.00020


def _meta(row: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(row)
    try:
        return json.loads(row.get("meta") or "{}")
    except (TypeError, ValueError):
        return {}


def breakdown(row: Dict[str, Any], gross_r: float | None = None,
              closed_at: int | None = None) -> Dict[str, float | bool]:
    """Bir kapanışın tahmini net R bileşenlerini döndürür."""
    row = dict(row)
    gross = float(row.get("result_r") if gross_r is None else gross_r)
    meta = _meta(row)
    risk = abs(float(row.get("entry") or 0) - float(row.get("stop") or 0))
    entry = float(row.get("entry") or 0)
    basis = float(meta.get("cost_basis") or (entry / risk if risk > 0 else 0))

    fee = meta.get("fee_cost_r")
    estimated = False
    if fee is None:
        # Eski intraday kayıtlarında maliyet alanı yoktu. Bunları sıfır
        # maliyetle "kârlı" göstermektense aynı muhafazakâr round-trip
        # varsayımıyla işaretli tahmin üret.
        if str(row.get("source") or "") in {"hunter15m", "test1h"} and basis > 0:
            fee = FALLBACK_ROUNDTRIP_PCT * basis
            estimated = True
        else:
            fee = 0.0
    fee = float(fee)

    funding_projected = float(meta.get("funding_cost_r") or 0.0)
    horizon_days = max(float(meta.get("horizon_days") or 14.0), 1 / 24)
    end = int(closed_at or row.get("closed_at") or db.now_ms())
    created = row.get("created_at")
    start = int(end if created is None else created)
    held_days = max(0.0, (end - start) / MS_DAY)
    funding_fraction = min(1.0, held_days / horizon_days)
    funding = funding_projected * funding_fraction
    cost = fee + funding
    return {
        "gross_r": round(gross, 6),
        "fee_r": round(fee, 6),
        "funding_r": round(funding, 6),
        "cost_r": round(cost, 6),
        "net_r": round(gross - cost, 6),
        "held_days": round(held_days, 5),
        "estimated": bool(estimated or funding_projected != 0),
    }


def close_values(row: Dict[str, Any], gross_r: float, closed_at: int) -> Dict[str, float | bool]:
    return breakdown(row, gross_r, closed_at)


def backfill() -> int:
    """Eski kapanmış kayıtları silmeden V4 net alanlarını tamamlar."""
    rows = db.query(
        "SELECT * FROM research_trades WHERE status='closed' AND result_r IS NOT NULL "
        "AND net_result_r IS NULL")
    updates = []
    for row in rows:
        x = breakdown(row)
        updates.append((x["gross_r"], x["cost_r"], x["net_r"], row["id"]))
    db.executemany(
        "UPDATE research_trades SET gross_result_r=?,cost_r=?,net_result_r=? WHERE id=?",
        updates)
    return len(updates)


def values(rows: Iterable[Dict[str, Any]]) -> list[float]:
    out = []
    for raw in rows:
        r = dict(raw)
        out.append(float(r.get("net_result_r") if r.get("net_result_r") is not None
                         else breakdown(r)["net_r"]))
    return out
