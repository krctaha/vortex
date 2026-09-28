"""Ana motorun intraday Avcı alt katmanı — yalnız araştırma/forward test.

Canlı emir açmaz ve ``trades`` tablosuna yazmaz. Ölçülmüş pozitif kanıt
oluşmadan yeni bir stratejiyi parayla aynalamak bilinçli olarak engellidir.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from .. import db, runtime
from ..strategies import breakout_hunter as strat
from . import binance, feature_store, research_metrics

log = logging.getLogger("vortex.hunter")
SOURCE = "hunter15m"
INTERVAL = "15m"
BARS = 220
MS_HOUR = 3_600_000

_task: Optional[asyncio.Task] = None
_lock = asyncio.Lock()
_state: Dict[str, Any] = {
    "running": False, "last_cycle_at": None, "last_duration": None,
    "last_error": None, "last_scanned": 0, "last_ready": 0,
    "last_opened": 0, "last_closed": 0, "watching": [], "top": [],
    "block_reasons": {},
}


def _cfg() -> Dict[str, Any]:
    return runtime.get()["hunter"]


def status() -> Dict[str, Any]:
    open_n = db.query_one(
        "SELECT COUNT(*) c FROM research_trades WHERE source=? AND status='open'", (SOURCE,))
    closed = db.query(
        "SELECT COALESCE(net_result_r,result_r) result_r,exit_reason FROM research_trades WHERE source=? AND status='closed' "
        "AND result_r IS NOT NULL ORDER BY closed_at", (SOURCE,))
    rs = [float(x["result_r"]) for x in closed]
    mean = sum(rs) / len(rs) if rs else 0.0
    sd = (sum((x - mean) ** 2 for x in rs) / (len(rs) - 1)) ** .5 if len(rs) > 1 else 0.0
    se = sd / math.sqrt(len(rs)) if rs else 0.0
    return {
        **_state, "enabled": bool(_cfg().get("enabled", True)),
        "live_enabled": False,
        "mode": "forward_test_only",
        "open": int(open_n["c"]) if open_n else 0,
        "closed": len(rs), "total_r": round(sum(rs), 3),
        "avg_r": round(mean, 4) if rs else None,
        "ci95": ([round(mean - 1.96 * se, 4), round(mean + 1.96 * se, 4)]
                 if len(rs) >= 5 and se > 0 else None),
        "exits": dict(Counter(x["exit_reason"] or "?" for x in closed)),
        "config": _cfg(),
    }


def _cooldown(symbol: str, hours: float) -> bool:
    row = db.query_one(
        "SELECT created_at FROM research_trades WHERE source=? AND symbol=? "
        "ORDER BY created_at DESC LIMIT 1", (SOURCE, symbol))
    return bool(row and db.now_ms() - int(row["created_at"]) < hours * MS_HOUR)


async def _close_due() -> int:
    rows = db.query(
        "SELECT * FROM research_trades WHERE source=? AND status='open'", (SOURCE,))
    if not rows or binance.status().get("mode") != "live":
        return 0
    now = db.now_ms()
    closed = 0
    for raw in rows:
        row = dict(raw)
        try:
            bars, tick = await asyncio.gather(
                binance.klines(row["symbol"], INTERVAL, 80),
                binance.ticker_24h(row["symbol"]))
        except Exception:  # noqa: BLE001
            continue
        direction = 1 if row["side"] == "LONG" else -1
        risk = abs(float(row["entry"]) - float(row["stop"]))
        if risk <= 0:
            continue
        relevant = [b for b in bars if int(b[0]) >= int(row["created_at"])]
        hit = None
        exit_price = None
        mfe = float(row.get("mfe_r") or 0)
        mae = float(row.get("mae_r") or 0)
        for b in relevant:
            high, low = float(b[2]), float(b[3])
            best = ((high - row["entry"]) if direction > 0 else (row["entry"] - low)) / risk
            worst = ((low - row["entry"]) if direction > 0 else (row["entry"] - high)) / risk
            mfe, mae = max(mfe, best), min(mae, worst)
            stop_touch = low <= row["stop"] if direction > 0 else high >= row["stop"]
            target_touch = high >= row["target"] if direction > 0 else low <= row["target"]
            if stop_touch:
                hit, exit_price = "stop", float(row["stop"])
                break
            if target_touch:
                hit, exit_price = "target", float(row["target"])
                break
        if hit is None and now >= int(row["expires_at"]):
            hit = "sure"
            exit_price = float(tick.get("lastPrice") or row["entry"])
        if hit is None:
            db.execute("UPDATE research_trades SET last_checked_at=?,mfe_r=?,mae_r=? WHERE id=?",
                       (now, round(mfe, 3), round(mae, 3), row["id"]))
            continue
        result_r = round((exit_price - row["entry"]) * direction / risk, 3)
        net = research_metrics.close_values(row, result_r, now)
        db.execute(
            "UPDATE research_trades SET status='closed',closed_at=?,exit_price=?,result_r=?,"
            "gross_result_r=?,cost_r=?,net_result_r=?,"
            "outcome=?,exit_reason=?,last_checked_at=?,mfe_r=?,mae_r=? WHERE id=?",
            (now, exit_price, result_r, net["gross_r"], net["cost_r"], net["net_r"],
             "win" if net["net_r"] > 0 else "loss" if net["net_r"] < 0 else "flat",
             hit, now, round(mfe, 3), round(mae, 3), row["id"]))
        closed += 1
        log.info("AVCI KAPANDI %s %s brut=%.3fR net=%.3fR", row["symbol"], hit,
                 result_r, net["net_r"])
    return closed


def _save(d: Dict[str, Any], cfg: Dict[str, Any]) -> int:
    now = db.now_ms()
    risk = abs(float(d["entry"]) - float(d["stop"]))
    cost_basis = float(d["entry"]) / risk if risk > 0 else 0.0
    fee_cost_r = research_metrics.FALLBACK_ROUNDTRIP_PCT * cost_basis
    meta = {**d, "setup_source": "intraday_breakout_reclaim",
            "live_enabled": False, "forward_test": True,
            "cost_basis": round(cost_basis, 5), "fee_cost_r": round(fee_cost_r, 6),
            "funding_cost_r": 0.0, "horizon_days": float(cfg["hold_hours"]) / 24.0}
    return db.execute(
        "INSERT INTO research_trades(symbol,interval,side,score,entry,stop,target,reasons,"
        "status,created_at,expires_at,source,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (d["symbol"], INTERVAL, d["side"], int(round(d["score"] * 10)),
         d["entry"], d["stop"], d["target"],
         json.dumps([f"AVCI reclaim skor={d['score']:.2f}",
                     f"hacim x{d['breakout_rel_volume']:.1f}/x{d['reclaim_rel_volume']:.1f}"],
                    ensure_ascii=False),
         "open", now, now + int(float(cfg["hold_hours"]) * MS_HOUR),
         SOURCE, json.dumps(meta, ensure_ascii=False)))


async def cycle_once() -> Dict[str, Any]:
    if _lock.locked():
        return {"ok": True, "skipped": "avcı taraması sürüyor"}
    async with _lock:
        started = time.monotonic()
        try:
            cfg = _cfg()
            closed = await _close_due()
            if not cfg.get("enabled", True) or binance.status().get("mode") != "live":
                return {"ok": True, "skipped": "kapalı veya canlı veri yok", "closed": closed}
            tickers, contracts = await asyncio.gather(
                binance.ticker_24h(), binance.perpetual_symbols())
            now = db.now_ms()
            min_age = int(cfg["min_listing_days"]) * 86_400_000
            allowed = {x["symbol"] for x in contracts
                       if not x.get("onboardDate") or now - int(x["onboardDate"]) >= min_age}
            liquid = [x for x in tickers if x.get("symbol") in allowed
                      and float(x.get("quoteVolume", 0) or 0) >= float(cfg["min_quote_volume_m"]) * 1e6]
            liquid.sort(key=lambda x: -float(x.get("quoteVolume", 0) or 0))
            universe = [x["symbol"] for x in liquid[:int(cfg["universe_size"])]]
            ticker_map = {x["symbol"]: x for x in liquid}
            open_symbols = {x["symbol"] for x in db.query(
                "SELECT symbol FROM research_trades WHERE source=? AND status='open'", (SOURCE,))}
            ready: List[Dict[str, Any]] = []
            watching: List[str] = []
            blocks: Counter = Counter()
            feature_items: List[Dict[str, Any]] = []
            sem = asyncio.Semaphore(8)

            async def inspect(symbol: str) -> None:
                blocked = symbol in open_symbols or _cooldown(symbol, float(cfg["cooldown_hours"]))
                async with sem:
                    try:
                        rows = await binance.klines(symbol, INTERVAL, BARS)
                        rows = rows[:-1] if rows else []
                        d = await asyncio.to_thread(strat.evaluate, symbol, rows, cfg)
                    except Exception:  # noqa: BLE001
                        blocks["veri/analiz hatası"] += 1
                        return
                feature_items.append({"symbol": symbol, "rows": rows,
                                      "ticker": ticker_map.get(symbol, {}), "hunter": d})
                if blocked:
                    return
                if d.get("ok"):
                    ready.append(d)
                elif d.get("phase") == "breakout_watch":
                    watching.append(symbol)
                else:
                    key = str((d.get("veto") or ["filtre"])[0]).split("(")[0].strip()
                    blocks[key] += 1

            await asyncio.gather(*(inspect(s) for s in universe))
            feature_result = await asyncio.to_thread(feature_store.capture_cycle, feature_items)
            ready.sort(key=lambda x: -float(x["score"]))
            open_n = int((db.query_one(
                "SELECT COUNT(*) c FROM research_trades WHERE source=? AND status='open'", (SOURCE,))
                or {"c": 0})["c"])
            opened = 0
            for d in ready:
                if opened >= int(cfg["max_new_per_cycle"]) or open_n + opened >= int(cfg["max_open"]):
                    break
                _save(d, cfg)
                opened += 1
                log.info("AVCI YAKALADI %s %s entry=%s stop=%s skor=%.2f",
                         d["symbol"], d["side"], d["entry"], d["stop"], d["score"])
            _state.update(last_cycle_at=db.now_ms(), last_duration=round(time.monotonic()-started, 2),
                          last_error=None, last_scanned=len(universe), last_ready=len(ready),
                          last_opened=opened, last_closed=closed, watching=watching[:20],
                          feature_store=feature_result,
                          top=[{k:d.get(k) for k in ("symbol","side","score","entry","stop_pct")}
                               for d in ready[:10]], block_reasons=dict(blocks.most_common(6)))
            log.info("Avcı tarama: evren=%d izleme=%d hazır=%d açılan=%d kapanan=%d",
                     len(universe), len(watching), len(ready), opened, closed)
            return {"ok": True, "scanned": len(universe), "watching": len(watching),
                    "ready": len(ready), "opened": opened, "closed": closed}
        except Exception as exc:  # noqa: BLE001
            _state["last_error"] = f"{type(exc).__name__}: {exc}"
            log.exception("Avcı taraması başarısız")
            return {"ok": False, "error": _state["last_error"]}


async def _loop() -> None:
    await asyncio.sleep(90)
    while True:
        try:
            await cycle_once()
            await asyncio.sleep(max(60, int(_cfg()["cycle_seconds"])))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Avcı döngüsü kırıldı")
            await asyncio.sleep(120)


async def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())
        _state["running"] = True
        log.info("Ana motor Avcı katmanı başladı — 15m breakout/reclaim, yalnız forward test")


async def stop() -> None:
    global _task
    _state["running"] = False
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _task = None
