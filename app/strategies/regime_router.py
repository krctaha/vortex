"""V4 rejim yönlendiricisi — gölge challenger karar katmanı.

Bu modül kâr olasılığı uydurmaz. Kapanmış barlardan piyasa rejimini ölçer
ve bir strateji ailesinin o rejimde mantıksal olarak uygun olup olmadığını
işaretler. İlk sürümde champion TSMOM'u ENGELLEMEZ; aynı gerçek işlemlerin
sonuçları üzerinden challenger alt kümesi ayrıca ölçülür.
"""
from __future__ import annotations

from typing import Any, Dict, Sequence

import numpy as np

from ..indicators import regime as regime_mod


DEFAULTS = {
    "min_confidence": 0.67,
    "max_volatility_rank": 0.95,
    "allow_transition": False,
    "allow_range": False,
}


def assess_tsmom(decision: Dict[str, Any], rows: Sequence[Sequence],
                 cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    c = {**DEFAULTS, **(cfg or {})}
    if len(rows) < 60:
        return {"allowed": False, "regime": "bilinmiyor", "route": "veri_yetersiz",
                "reasons": ["rejim için en az 60 kapanmış bar gerekli"]}

    high = np.asarray([float(x[2]) for x in rows], dtype=float)
    low = np.asarray([float(x[3]) for x in rows], dtype=float)
    close = np.asarray([float(x[4]) for x in rows], dtype=float)
    reg = regime_mod.classify(high, low, close)
    name = str(reg.get("regime") or "bilinmiyor")
    side = str(decision.get("side") or "")
    aligned = ((name == "trend_yukari" and side == "LONG")
               or (name == "trend_asagi" and side == "SHORT"))
    reasons = []

    if name in {"trend_yukari", "trend_asagi"}:
        route = "trend_tsmom" if aligned else "trend_tersi"
        if not aligned:
            reasons.append("TSMOM yönü ölçülen trend rejimiyle ters")
    elif name == "sikisik":
        route = "range_bekle"
        if not bool(c["allow_range"]):
            reasons.append("sıkışık rejimde trend stratejisi kapalı")
    elif name == "gecis":
        route = "gecis_bekle"
        if not bool(c["allow_transition"]):
            reasons.append("geçiş rejiminde yeni trend girişi kapalı")
    else:
        route = "veri_yetersiz"
        reasons.append("rejim ölçülemedi")

    confidence = float(reg.get("confidence") or 0)
    if name.startswith("trend_") and confidence < float(c["min_confidence"]):
        reasons.append(f"rejim güveni düşük ({confidence:.2f})")
    vol_rank = float(reg.get("volatility_rank") or 0)
    if vol_rank > float(c["max_volatility_rank"]):
        reasons.append(f"oynaklık kendi geçmişinin tepesinde (%{vol_rank*100:.0f})")

    allowed = not reasons
    return {
        "allowed": allowed,
        "regime": name,
        "regime_label": regime_mod.REGIME_LABEL.get(name, name),
        "route": route,
        "side": side,
        "aligned": aligned,
        "confidence": round(confidence, 3),
        "volatility_rank": round(vol_rank, 3),
        "adx": reg.get("adx"),
        "choppiness": reg.get("choppiness"),
        "efficiency_ratio": reg.get("efficiency_ratio"),
        "reasons": reasons,
        "mode": "shadow",
    }
