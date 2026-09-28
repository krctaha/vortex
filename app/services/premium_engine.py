"""SMC/ICT research scanner. Legacy /premium URLs remain compatible."""
from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import Any, Dict
from .. import db
from . import smc_ict_engine, binance, smc_journal, smc_quality, smc_controls, smc_rsi

log = logging.getLogger(__name__)
_STATE: Dict[str, Any] = {
    "enabled": True, "mode": "research", "live_execution": False,
    "last_scan_at": None, "scanning": False, "candidates": [], "scanned": 0,
    "target_count": 200, "results": [], "next_scan_at": None,
}
_task = None

def _init_history():
    db.execute("""CREATE TABLE IF NOT EXISTS smc_observations (
        symbol TEXT NOT NULL, as_of INTEGER NOT NULL, demo INTEGER NOT NULL,
        payload TEXT NOT NULL, PRIMARY KEY(symbol,as_of,demo))""")

def status() -> Dict[str, Any]:
    stale = not _STATE.get("last_scan_at") or time.time() * 1000 - _STATE["last_scan_at"] > 20 * 60_000
    candidates = [{**r, "ready": False, "phase": "VERI_BAYAT"} if stale else r
                  for r in _STATE.get("candidates", [])]
    if candidates and _STATE.get("score_version") == smc_quality.VERSION:
        ids=[smc_journal.setup_id(r) for r in candidates]
        records=db.query("SELECT id,state FROM smc_paper WHERE id IN ("+','.join('?' for _ in ids)+")",ids)
        states={r["id"]:r["state"] for r in records}
        candidates=[{**r,"ready":False,"phase":"TAKIPTE" if states[smc_journal.setup_id(r)] == "open" else "KURULUM_GECERSIZ"}
                    if smc_journal.setup_id(r) in states and states[smc_journal.setup_id(r)] != "pending" else r for r in candidates]
    direction = _STATE.get("direction", "AUTO")
    candidates = [{**r, "selected": False, "direction_allowed": False} if not smc_controls.allows(r, direction) else {**r, "direction_allowed": True} for r in candidates]
    return {**_STATE, "progress": dict(smc_ict_engine.scan_progress), "candidates": candidates, "stale": stale, "engine": "smc_ict_v5", "direction": direction,
            "demo": binance.is_demo(), "htf_interval": "4h", "ltf_interval": "15m"}

def set_enabled(value: bool) -> None:
    _STATE["enabled"] = bool(value)
    db.set_setting("smc_enabled", bool(value))

def set_direction(value: str) -> None:
    if value not in ("AUTO", "LONG", "SHORT"):
        raise ValueError("Geçersiz yön")
    db.set_setting("smc_direction", value)
    _STATE["direction"] = value
    _STATE["candidates"] = smc_journal.rank([dict(row) for row in _STATE.get("candidates", [])])
    if not status()["stale"]:
        smc_journal.register(status()["candidates"])

async def open_live(candidate: Dict[str, Any], user_id: int, confirmation: str) -> Dict[str, Any]:
    """Client fields can never confer execution authority."""
    return {"ok": False, "reason": "SMC/ICT araştırma sürümünde canlı emir kapalı."}

def history():
    _init_history()
    rows = db.query("SELECT payload FROM smc_observations ORDER BY as_of DESC LIMIT 50")
    return {"items": [json.loads(row["payload"]) for row in rows],
            "note": "Gözlem günlüğüdür; gerçekleşmiş işlem veya performans karnesi değildir."}

async def scan_once(limit: int = 200, interval: str = "15m", user_id: int | None = None):
    if not _STATE["enabled"] or _STATE["scanning"]:
        return status()
    # Shared cooldown prevents multiple users from exhausting exchange quota.
    if _STATE["last_scan_at"] and time.time()*1000 - _STATE["last_scan_at"] < 60_000:
        return status()
    _STATE["scanning"] = True
    try:
        result = await smc_ict_engine.scan(limit, "4h", interval)
        for row in result.get('results',[]):
            if not row.get('demo'):smc_rsi.install(row['symbol'],row.get('rsi_seed'))
        result["candidates"] = smc_journal.rank(result["candidates"])
        smc_journal.register(result["candidates"])
        _init_history()
        for row in result["candidates"]:
            db.execute("INSERT OR IGNORE INTO smc_observations VALUES(?,?,?,?)",
                       (row["symbol"], row["as_of"], int(row["demo"]),
                        json.dumps(row, ensure_ascii=False, allow_nan=False)))
        _STATE.update({key: result[key] for key in ("candidates", "scanned", "errors", "last_scan_at")})
        _STATE.update({key: result.get(key, [] if key == "results" else 0)
                       for key in ("results", "reused", "unique_analyzed")})
        _STATE["next_scan_at"] = (int(time.time() // 900) + 1) * 900_000 + 5000
        if result.get("errors"):
            _STATE["next_scan_at"] = min(_STATE["next_scan_at"], int(time.time()*1000)+60_000)
        _STATE["last_error"] = None
        db.set_setting("smc_last_scan", {key: value for key, value in _STATE.items() if key != "scanning"})
        from . import smc_notifications
        smc_notifications.wake()
        return status()
    finally:
        _STATE["scanning"] = False

async def _loop():
    while True:
        try:
            if time.time() * 1000 >= (_STATE.get("next_scan_at") or 0):
                await scan_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _STATE["last_error"] = type(exc).__name__
            _STATE["next_scan_at"] = int(time.time()*1000)+60_000
            log.warning("SMC scan failed: %s", type(exc).__name__)
        await asyncio.sleep(5)

async def start():
    global _task
    if _task and not _task.done():
        return
    _init_history()
    saved = db.get_setting("smc_last_scan", {})
    if isinstance(saved, dict):
        if saved.get("data_mode") == binance.settings.data_mode and saved.get("score_version") == smc_quality.VERSION:
            _STATE.update({k: v for k, v in saved.items() if k in ("candidates", "results", "scanned", "errors", "last_scan_at", "next_scan_at")})
    _STATE["data_mode"] = binance.settings.data_mode
    _STATE["score_version"] = smc_quality.VERSION
    _STATE["direction"] = smc_controls.direction()
    if _STATE.get("results"):
        from . import binance_ws
        binance_ws.set_watch([r["symbol"] for r in _STATE["results"]])
        for row in _STATE['results']:
            if not row.get('demo'):smc_rsi.install(row['symbol'],row.get('rsi_seed'))
        if not binance.is_demo() and any(not r.get('rsi_seed') for r in _STATE['results']):
            _STATE['next_scan_at']=0  # First RSI rollout warms through the normal bounded scan.
    _STATE["enabled"] = bool(db.get_setting("smc_enabled", True))
    await smc_journal.start()
    _STATE["candidates"] = smc_journal.rank([dict(row) for row in _STATE.get("candidates", [])])
    _task = asyncio.create_task(_loop())

async def stop():
    global _task
    await smc_journal.stop()
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
