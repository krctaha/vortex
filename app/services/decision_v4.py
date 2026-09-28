"""V4 champion–challenger karar kayıt ve kanıt katmanı."""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List

from .. import db, runtime


def router_config() -> Dict[str, Any]:
    c = runtime.get()["decision_v4"]
    return {
        "min_confidence": c["router_min_confidence"],
        "max_volatility_rank": c["router_max_volatility_rank"],
        "allow_transition": c["router_allow_transition"],
        "allow_range": c["router_allow_range"],
    }


def record(research_id: int, decision: Dict[str, Any]) -> None:
    v4 = decision.get("v4") or {}
    if not v4:
        return
    db.execute(
        "INSERT OR IGNORE INTO decision_audit(research_id,strategy,symbol,side,regime,"
        "route,router_allowed,created_at,details) VALUES(?,?,?,?,?,?,?,?,?)",
        (research_id, "tsmom", decision["symbol"], decision["side"],
         str(v4.get("regime") or "bilinmiyor"), str(v4.get("route") or "?"),
         int(bool(v4.get("allowed"))), db.now_ms(),
         json.dumps(v4, ensure_ascii=False)))


def _stats(values: List[float]) -> Dict[str, Any]:
    n = len(values)
    if not n:
        return {"n": 0, "avg_r": None, "total_r": 0.0, "ci95": None,
                "win_rate": None, "profit_factor": None}
    mean = sum(values) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in values) / (n - 1)) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else 0.0
    wins = [x for x in values if x > 0]
    losses = [x for x in values if x < 0]
    return {
        "n": n, "avg_r": round(mean, 4), "total_r": round(sum(values), 3),
        "ci95": ([round(mean - 1.96 * se, 4), round(mean + 1.96 * se, 4)]
                 if n > 1 else None),
        "win_rate": round(len(wins) / n * 100, 1),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses else None,
    }


def status() -> Dict[str, Any]:
    cfg = runtime.get()["decision_v4"]
    rows = db.query(
        "SELECT a.router_allowed,a.regime,a.route,r.net_result_r,r.result_r "
        "FROM decision_audit a LEFT JOIN research_trades r ON r.id=a.research_id "
        "WHERE r.status='closed' AND r.result_r IS NOT NULL ORDER BY r.closed_at")
    allowed = [float(r["net_result_r"] if r["net_result_r"] is not None else r["result_r"])
               for r in rows if r["router_allowed"]]
    rejected = [float(r["net_result_r"] if r["net_result_r"] is not None else r["result_r"])
                for r in rows if not r["router_allowed"]]
    all_audits = db.query_one("SELECT COUNT(*) c FROM decision_audit") or {"c": 0}
    regimes = db.query(
        "SELECT regime,COUNT(*) n FROM decision_audit GROUP BY regime ORDER BY n DESC")
    return {
        "mode": cfg["mode"],
        "live_requires_validation": bool(cfg["require_validation_for_live"]),
        "quarantined_live_sides": cfg["quarantined_live_sides"],
        "audited": int(all_audits["c"]),
        "challenger_allowed": _stats(allowed),
        "challenger_rejected": _stats(rejected),
        "regimes": {r["regime"]: int(r["n"]) for r in regimes},
        "config": cfg,
    }


def strict_live_gate(side: str, decision: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Canlı para için yön, rejim ve ileri-test doğrulaması.

    İleri-test zorunluluğu riskli profil için kapatılsa bile rejim
    yönlendiricisi veto edilmiş kurulumu borsaya geçiremez.
    """
    from . import validation  # validation -> tsmom -> decision_v4 döngüsünü kır
    cfg = runtime.get()["decision_v4"]
    side = str(side).upper()
    if side in cfg["quarantined_live_sides"]:
        return {"allowed": False, "reasons": [f"{side} V4 canlı karantinasında"]}
    if not cfg["require_validation_for_live"]:
        if not decision or not bool(decision.get("allowed")):
            return {"allowed": False,
                    "reasons": ["V4 rejim yönlendiricisi kurulumu onaylamadı"]}
        return {"allowed": True, "reasons": []}
    gate = validation.gate(side, honor_manual_override=False)
    return {"allowed": bool(gate["allowed"]), "reasons": list(gate["reasons"]),
            "checks": gate["checks"]}
