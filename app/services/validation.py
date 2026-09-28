"""TSMOM performans ölçümü ve motor kontrol merkezi.

Araştırma, kâğıt portföyü ve dış bildirim aynı şey değildir. Bu modül
yalnızca kapanmış ileri-test kayıtlarını kullanarak sistemin ölçülen
performansını raporlar. Telegram bildirim izni burada verilmez veya kesilmez.
Geçmiş öz-test raporda görünür ama ileri-test yerine geçmez.
"""
from __future__ import annotations

import math
import json
from typing import Any, Dict, List, Optional

from .. import db, runtime
from . import binance, binance_ws

SOURCE = "tsmom"


def _rows(side: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = (
        "SELECT side,COALESCE(net_result_r,result_r) result_r,exit_reason,created_at,closed_at "
        "FROM research_trades "
        "WHERE source=? AND status='closed' AND result_r IS NOT NULL "
        "AND COALESCE(exit_reason,'') <> 'elle'"
    )
    params: List[Any] = [SOURCE]
    if side in ("LONG", "SHORT"):
        sql += " AND side=?"
        params.append(side)
    sql += " ORDER BY closed_at"
    return [dict(r) for r in db.query(sql, params)]


def _stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    values = [float(r["result_r"]) for r in rows]
    n = len(values)
    if not n:
        return {"n": 0, "entry_days": 0, "avg_r": None, "total_r": 0.0, "win_rate": None,
                "profit_factor": None, "ci95": None, "max_drawdown_r": 0.0}

    mean = sum(values) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in values) / (n - 1)) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else 0.0
    wins = [x for x in values if x > 0]
    losses = [x for x in values if x < 0]
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return {
        "n": n,
        "entry_days": len({int(r["created_at"]) // 86_400_000 for r in rows}),
        "avg_r": round(mean, 4),
        "total_r": round(sum(values), 2),
        "win_rate": round(len(wins) / n * 100, 1),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses else None,
        "ci95": [round(mean - 1.96 * se, 4), round(mean + 1.96 * se, 4)] if n > 1 else None,
        "max_drawdown_r": round(drawdown, 2),
    }


def gate(side: Optional[str] = None, honor_manual_override: bool = True) -> Dict[str, Any]:
    """Canlı para doğrulaması için ileri-test yeterliliğini hesapla.

    ``honor_manual_override`` yalnız eski çağrılarla uyumluluk için korunur;
    manuel Telegram istisnası kaldırılmıştır. Sonuç her zaman aynı, katı
    ölçütlerden gelir. Bu bir kâr garantisi değil, asgari kalite filtresidir.
    """
    ccfg = runtime.get()["copy"]
    overall = _stats(_rows())
    directional = _stats(_rows(side)) if side in ("LONG", "SHORT") else None
    min_closed = int(ccfg.get("validation_min_closed", 30))
    min_days = int(ccfg.get("validation_min_observation_days", 20))
    min_side = int(ccfg.get("validation_min_side_closed", 10))
    max_dd = float(ccfg.get("validation_max_drawdown_r", 6.0))
    require_ci = bool(ccfg.get("validation_require_ci_positive", True))

    checks = []

    def add(code: str, label: str, passed: bool, current: Any, required: Any) -> None:
        checks.append({"code": code, "label": label, "passed": bool(passed),
                       "current": current, "required": required})

    add("sample", "İleri-test örneği", overall["n"] >= min_closed,
        overall["n"], f">= {min_closed}")
    add("observation_days", "Bağımsız giriş günü",
        overall["entry_days"] >= min_days, overall["entry_days"], f">= {min_days}")
    ci_low = overall["ci95"][0] if overall.get("ci95") else None
    add("ci", "%95 güven aralığı alt sınırı",
        (ci_low is not None and ci_low > 0) if require_ci else True,
        ci_low, "> 0" if require_ci else "kapalı")
    add("drawdown", "Maksimum düşüş",
        abs(float(overall["max_drawdown_r"] or 0)) <= max_dd,
        overall["max_drawdown_r"], f">= -{max_dd:g}R")

    if directional is not None:
        add("side_sample", f"{side} yön örneği", directional["n"] >= min_side,
            directional["n"], f">= {min_side}")
        add("side_edge", f"{side} ortalama sonucu",
            directional["avg_r"] is not None and directional["avg_r"] > 0,
            directional["avg_r"], "> 0R")

    allowed = all(item["passed"] for item in checks)
    reasons = [item["label"] for item in checks if not item["passed"]]
    return {
        "allowed": allowed,
        "state": "READY" if allowed else "INSUFFICIENT",
        "side": side,
        "checks": checks,
        "reasons": reasons,
        "overall": overall,
        "direction": directional,
    }


def overview() -> Dict[str, Any]:
    """Motor Merkezi için katmanları birbirine karıştırmadan özetle."""
    # Gec import: tsmom_engine bu modulu yayin kapisi icin kullaniyor.
    from . import telegram, tsmom_engine

    trade_rows = [dict(r) for r in db.query(
        "SELECT status,mode,note,meta FROM trades WHERE status IN ('candidate','open')")]
    tsmom_trades = []
    for row in trade_rows:
        try:
            meta = json.loads(row.get("meta") or "{}")
        except (TypeError, ValueError):
            meta = {}
        if meta.get("engine") == SOURCE or str(row.get("note") or "").startswith("TSMOM"):
            tsmom_trades.append(row)

    counts = {
        "research_open": int((db.query_one(
            "SELECT COUNT(*) c FROM research_trades WHERE source=? AND status='open'",
            (SOURCE,)) or {"c": 0})["c"]),
        "research_closed": int((db.query_one(
            "SELECT COUNT(*) c FROM research_trades WHERE source=? AND status='closed' "
            "AND result_r IS NOT NULL AND COALESCE(exit_reason,'') <> 'elle'",
            (SOURCE,)) or {"c": 0})["c"]),
        "research_manual_closed": int((db.query_one(
            "SELECT COUNT(*) c FROM research_trades WHERE source=? AND status='closed' "
            "AND exit_reason='elle'", (SOURCE,)) or {"c": 0})["c"]),
        "research_invalid": int((db.query_one(
            "SELECT COUNT(*) c FROM research_trades WHERE source=? AND status='invalid'",
            (SOURCE,)) or {"c": 0})["c"]),
        "test_open": int((db.query_one(
            "SELECT COUNT(*) c FROM research_trades WHERE source='test1h' AND status='open'")
            or {"c": 0})["c"]),
        "test_closed": int((db.query_one(
            "SELECT COUNT(*) c FROM research_trades WHERE source='test1h' AND status='closed'")
            or {"c": 0})["c"]),
        "paper_open": sum(1 for x in tsmom_trades
                          if x["status"] == "open" and x["mode"] == "paper"),
        "live_open": sum(1 for x in tsmom_trades
                         if x["status"] == "open" and x["mode"] == "live"),
        "candidates": sum(1 for x in tsmom_trades if x["status"] == "candidate"),
    }
    recent = [dict(r) for r in db.query(
        "SELECT id,symbol,side,status,entry,stop,target,"
        "COALESCE(net_result_r,result_r) result_r,gross_result_r,cost_r,"
        "exit_reason,created_at,closed_at "
        "FROM research_trades WHERE source=? ORDER BY created_at DESC LIMIT 12", (SOURCE,))]
    admin = db.query_one(
        "SELECT telegram_token,telegram_chat_id FROM users "
        "WHERE role='admin' AND is_active=1 ORDER BY id LIMIT 1") or {}
    return {
        "pipeline": counts,
        "validation": {"overall": gate(), "long": gate("LONG"), "short": gate("SHORT")},
        "health": {
            "data": binance.status(),
            "stream": binance_ws.status(),
            "telegram_configured": telegram.configured(
                admin.get("telegram_token", ""), admin.get("telegram_chat_id", "")),
            "signal_notifications_enabled": bool(
                runtime.get()["notifications"].get("send_signals", True)),
            "selftest": db.get_setting("tsmom_selftest", {}) or {},
        },
        "engine": tsmom_engine.control_summary(),
        "recent": recent,
    }
