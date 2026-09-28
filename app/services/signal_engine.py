"""VORTEX kâğıt sinyal tarayıcısı.

Gerçek emir göndermez. Likit USDT perpetual evrenini düzenli tarar, yalnızca
birbirinden farklı beş filtreden en az dördü uyduğunda aday kaydeder.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

from .. import db, runtime
from ..config import settings
from . import analysis, binance, telegram

log = logging.getLogger("vortex.engine")

SCAN_INTERVAL_SECONDS = 300
UNIVERSE_SIZE = 24
SIGNAL_COOLDOWN_MS = 6 * 60 * 60 * 1000

_task: Optional[asyncio.Task] = None
_scan_lock = asyncio.Lock()
_state: Dict[str, Any] = {
    "running": False, "scanning": False, "last_scan_at": None,
    "last_scan_duration_ms": None, "last_universe": 0,
    "last_qualified": 0, "last_error": None, "next_scan_at": None,
}


def enabled() -> bool:
    return bool(db.get_setting("signal_engine_enabled", False))


def set_enabled(value: bool) -> None:
    db.set_setting("signal_engine_enabled", bool(value))
    _state["next_scan_at"] = int(time.time() * 1000) if value else None


def _decode(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    for key in ("targets", "reasons"):
        try:
            out[key] = json.loads(out.get(key) or "[]")
        except json.JSONDecodeError:
            out[key] = []
    return out


def recent(limit: int = 20) -> List[Dict[str, Any]]:
    return [_decode(x) for x in db.query(
        "SELECT * FROM signal_events ORDER BY created_at DESC LIMIT ?", (limit,))]


def status() -> Dict[str, Any]:
    cfg = runtime.get()
    return {**_state, "enabled": enabled(),
            "interval_seconds": cfg["engine"]["scan_interval_seconds"],
            "universe_size": cfg["engine"]["universe_size"],
            "direction": cfg["engine"]["direction"],
            "signal_interval": cfg["engine"]["signal_interval"],
            "min_score": cfg["engine"]["min_score"], "recent": recent(12),
            "research": research_status()}


def _telegram_text(symbol: str, interval: str, plan: Dict[str, Any]) -> str:
    e = telegram.esc
    arrow = "🟢 LONG" if plan["side"] == "LONG" else "🔴 SHORT"
    lines = [
        f"<b>VORTEX · KÂĞIT SİNYAL</b>",
        f"<b>{e(symbol)}</b> · {arrow} · <code>{e(interval)}</code>", "",
        f"Giriş: <code>{e(plan['entry'])}</code>",
    ]
    for i, target in enumerate(plan["targets"], 1):
        lines.append(f"TP{i}: <code>{e(target)}</code>")
    lines.extend([
        f"SL: <code>{e(plan['stop'])}</code>", "",
        f"Filtre: <b>{e(plan['score'])}/{e(plan['max_score'])}</b>",
        " · ".join(e(x) for x in plan.get("reasons", [])),
        "", "⚠️ <i>Otomatik kâğıt senaryodur; gerçek emir değildir.</i>",
    ])
    return "\n".join(lines)


async def _save_signal(symbol: str, interval: str, plan: Dict[str, Any]) -> bool:
    cfg = runtime.get(); ecfg, rcfg, ncfg = cfg["engine"], cfg["risk"], cfg["notifications"]
    daily = db.query_one("SELECT COUNT(*) c FROM signal_events WHERE created_at>=?", (db.now_ms() - 86_400_000,))
    if ecfg["max_signals_per_day"] == 0 or (daily and daily["c"] >= ecfg["max_signals_per_day"]):
        return False
    cutoff = db.now_ms() - ecfg["cooldown_minutes"] * 60 * 1000
    duplicate = db.query_one(
        "SELECT id FROM signal_events WHERE symbol=? AND interval=? AND side=? AND created_at>=?",
        (symbol, interval, plan["side"], cutoff))
    if duplicate:
        return False

    admin = db.query_one("SELECT * FROM users WHERE role='admin' ORDER BY id LIMIT 1")
    user_id = admin["id"] if admin else None
    sid = db.execute(
        "INSERT INTO signal_events(symbol,interval,side,status,score,entry,stop,targets,reasons,created_at,user_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (symbol, interval, plan["side"], "candidate", plan["score"], plan["entry"],
         plan["stop"], json.dumps(plan["targets"]), json.dumps(plan["reasons"], ensure_ascii=False),
         db.now_ms(), user_id))

    if admin:
        leverage = rcfg["default_leverage"]
        stop_distance = abs(plan["entry"] - plan["stop"])
        risk_sized_margin = (rcfg["max_risk_per_trade"] * plan["entry"] /
                             (stop_distance * leverage)) if stop_distance and leverage else 0
        margin = round(min(rcfg["max_position_margin"], risk_sized_margin), 2)
        qty = margin * leverage / plan["entry"] if plan["entry"] else 0
        risk = abs(plan["entry"] - plan["stop"]) * qty
        db.execute(
            "INSERT INTO trades(user_id,symbol,side,status,mode,entry,stop,take_profit,margin_usdt,"
            "leverage,margin_type,qty,risk_usdt,opened_at,interval,note,meta) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (admin["id"], symbol, plan["side"], "candidate", "paper", plan["entry"],
             plan["stop"], plan["targets"][0], margin, leverage, "ISOLATED", qty, risk,
             db.now_ms(), interval, "Otomatik motor adayı",
             json.dumps({"signal_id": sid, "targets": plan["targets"],
                         "score": plan["score"], "reasons": plan["reasons"]}, ensure_ascii=False)))

        if ncfg["send_signals"] and telegram.configured(admin.get("telegram_token", ""), admin.get("telegram_chat_id", "")):
            result = await telegram.send(_telegram_text(symbol, interval, plan),
                                         token=admin["telegram_token"],
                                         chat_id=admin["telegram_chat_id"])
            if result.get("ok"):
                db.execute("UPDATE signal_events SET sent_at=? WHERE id=?", (db.now_ms(), sid))
    return True


def _research_rows(limit: int = 30) -> List[Dict[str, Any]]:
    rows = db.query("SELECT * FROM research_trades ORDER BY created_at DESC LIMIT ?", (limit,))
    for row in rows:
        try:
            row["reasons"] = json.loads(row.get("reasons") or "[]")
        except json.JSONDecodeError:
            row["reasons"] = []
    return rows


def research_status() -> Dict[str, Any]:
    rows = db.query("SELECT side,status,result_r,outcome FROM research_trades")
    closed = [x for x in rows if x["status"] == "closed" and x["result_r"] is not None]
    wins = sum(1 for x in closed if x["result_r"] > 0)
    by_side = {}
    for side in ("LONG", "SHORT"):
        sample = [x for x in closed if x["side"] == side]
        by_side[side] = {"count": len(sample),
                         "win_rate": round(sum(1 for x in sample if x["result_r"] > 0) / len(sample) * 100, 1) if sample else None,
                         "avg_r": round(sum(x["result_r"] for x in sample) / len(sample), 3) if sample else None}
    return {"open": sum(1 for x in rows if x["status"] == "open"), "closed": len(closed),
            "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
            "avg_r": round(sum(x["result_r"] for x in closed) / len(closed), 3) if closed else None,
            "by_side": by_side, "recent": _research_rows(20)}


def _evaluate_research(prices: Dict[str, float]) -> None:
    now = db.now_ms()
    for row in db.query("SELECT * FROM research_trades WHERE status='open'"):
        price = prices.get(row["symbol"])
        if not price:
            continue
        direction = 1 if row["side"] == "LONG" else -1
        risk = abs(row["entry"] - row["stop"])
        if not risk:
            continue
        r_now = (price - row["entry"]) * direction / risk
        hit_stop = r_now <= -1
        hit_target = (row["side"] == "LONG" and price >= row["target"]) or \
                     (row["side"] == "SHORT" and price <= row["target"])
        expired = now >= row["expires_at"]
        if not (hit_stop or hit_target or expired):
            continue
        result_r = -1.0 if hit_stop else round((row["target"] - row["entry"]) * direction / risk, 3) if hit_target else round(r_now, 3)
        outcome = "win" if result_r > 0 else "loss" if result_r < 0 else "flat"
        db.execute("UPDATE research_trades SET status='closed',closed_at=?,exit_price=?,result_r=?,outcome=? WHERE id=?",
                   (now, price, result_r, outcome, row["id"]))


async def _save_research(symbol: str, interval: str, plan: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    ecfg = cfg["engine"]; now = db.now_ms(); day_start = now - 24 * 60 * 60 * 1000
    count = db.query_one("SELECT COUNT(*) c FROM research_trades WHERE created_at>=?", (day_start,))
    if count and count["c"] >= ecfg["research_max_per_day"]:
        return False
    duplicate = db.query_one("SELECT id FROM research_trades WHERE symbol=? AND side=? AND status='open'", (symbol, plan["side"]))
    if duplicate:
        return False
    target = plan["targets"][1] if len(plan["targets"]) > 1 else plan["targets"][0]
    db.execute("INSERT INTO research_trades(symbol,interval,side,score,entry,stop,target,reasons,status,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (symbol, interval, plan["side"], plan["score"], plan["entry"], plan["stop"], target,
                json.dumps(plan.get("reasons", []), ensure_ascii=False), "open", now,
                now + ecfg["research_horizon_hours"] * 60 * 60 * 1000))
    if cfg["notifications"]["send_research"]:
        admin = db.query_one("SELECT * FROM users WHERE role='admin' ORDER BY id LIMIT 1")
        if admin and telegram.configured(admin.get("telegram_token", ""), admin.get("telegram_chat_id", "")):
            direction = "🟢 LONG" if plan["side"] == "LONG" else "🔴 SHORT"
            text = "\n".join([
                "<b>VORTEX · PAPER ARAŞTIRMA KAYDI</b>",
                f"<b>{telegram.esc(symbol)}</b> · {direction} · <code>{telegram.esc(interval)}</code>",
                f"Giriş <code>{telegram.esc(plan['entry'])}</code> · Stop <code>{telegram.esc(plan['stop'])}</code>",
                f"Araştırma hedefi <code>{telegram.esc(target)}</code> · skor {telegram.esc(plan['score'])}/5", "",
                "⚠️ <i>Bu bir sinyal değildir; yalnızca istatistik toplamak için açılan sanal testtir.</i>",
            ])
            await telegram.send(text, token=admin["telegram_token"], chat_id=admin["telegram_chat_id"], silent=True)
    return True


async def scan_once() -> Dict[str, Any]:
    if _scan_lock.locked():
        return {"ok": False, "error": "Tarama zaten çalışıyor"}
    async with _scan_lock:
        started = time.monotonic()
        _state.update(scanning=True, last_error=None)
        try:
            cfg = runtime.get(); ecfg = cfg["engine"]
            tickers, symbols = await asyncio.gather(binance.ticker_24h(), binance.perpetual_symbols())
            live_prices = {x["symbol"]: float(x.get("lastPrice", 0) or 0) for x in tickers}
            _evaluate_research(live_prices)
            allowed = {x["symbol"] for x in symbols}
            liquid = [x for x in tickers if x.get("symbol") in allowed and
                      float(x.get("quoteVolume", 0) or 0) >= 10_000_000]
            liquid.sort(key=lambda x: float(x.get("quoteVolume", 0) or 0), reverse=True)
            targets = [x["symbol"] for x in liquid[:ecfg["universe_size"]]]
            sem = asyncio.Semaphore(4)

            async def inspect(symbol: str):
                async with sem:
                    try:
                        snap = await analysis.snapshot(symbol, ecfg["signal_interval"], 300, include_series=False)
                        return symbol, snap.get("trade_plan") if snap.get("ok") else None
                    except Exception as exc:  # noqa: BLE001
                        log.debug("%s tarama hatası: %s", symbol, exc)
                        return symbol, None

            inspected = await asyncio.gather(*(inspect(s) for s in targets))
            qualified = 0
            created = 0
            candidates = []
            for symbol, plan in inspected:
                if not plan:
                    continue
                if ecfg["direction"] != "BOTH" and plan["side"] != ecfg["direction"]:
                    continue
                candidates.append((symbol, plan))
                if plan["score"] < ecfg["min_score"]:
                    continue
                qualified += 1
                if created >= ecfg["max_signals_per_scan"]:
                    continue
                if await _save_signal(symbol, ecfg["signal_interval"], plan):
                    created += 1
            research_created = 0
            if not created and ecfg["research_enabled"]:
                candidates.sort(key=lambda x: x[1]["score"], reverse=True)
                for symbol, plan in candidates:
                    if plan["score"] >= ecfg["research_min_score"] and await _save_research(symbol, ecfg["signal_interval"], plan, cfg):
                        research_created = 1
                        break
            now = db.now_ms()
            _state.update(last_scan_at=now, last_universe=len(targets),
                          last_qualified=qualified,
                          last_scan_duration_ms=int((time.monotonic() - started) * 1000),
                          next_scan_at=now + ecfg["scan_interval_seconds"] * 1000 if enabled() else None)
            return {"ok": True, "universe": len(targets), "qualified": qualified,
                    "created": created, "research_created": research_created,
                    "duration_ms": _state["last_scan_duration_ms"]}
        except Exception as exc:  # noqa: BLE001
            _state["last_error"] = f"{type(exc).__name__}: {exc}"
            log.exception("Sinyal taraması başarısız")
            return {"ok": False, "error": _state["last_error"]}
        finally:
            _state["scanning"] = False


async def _loop() -> None:
    _state["running"] = True
    try:
        while True:
            if enabled():
                due = _state.get("next_scan_at") or 0
                if db.now_ms() >= due:
                    await scan_once()
            await asyncio.sleep(5)
    finally:
        _state["running"] = False


async def start() -> None:
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop(), name="vortex-signal-engine")


async def stop() -> None:
    global _task
    if not _task:
        return
    _task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _task
    _task = None
