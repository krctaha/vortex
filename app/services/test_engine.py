"""Hizli Test Motoru — 1 saatlik dongu, YALNIZCA olcum.

Neden var
---------
Ana motor gunluk bar uzerinde calisiyor ve pozisyonu 14 gun tutuyor. Bu,
strateji icin dogru; ama OLCUM icin felaket: bir hafta bekleyip 2 sonuc
goruyorsun. Bir sayinin sifirdan farkli oldugunu gostermek icin yuzlerce
islem gerekiyor — o hizda yillar surerdi.

Bu motor ayni karar cekirdegini 1 SAATLIK barda calistiriyor: bir islem
acar, bir saat sonra kapatir, sonucu yazar. Gunde 24 gozlem uretir.

NE OLCTUGU KONUSUNDA DURUST OLMAK GEREKIYOR
-------------------------------------------
Bu motorun sonucu ana motorun sonucu DEGILDIR. Farkli ufuk, farkli
strateji. 1 saatlik momentumun kazanmasi 14 gunlukun kazanacagi anlamina
gelmez; kaybetmesi de kaybedecegi anlamina gelmez.

Ne icin iyi:
  - makinenin ucu uca calistigini kanitlamak (giris, cikis, muhasebe,
    karne) — hizli geri bildirimle
  - kaba hatalari yakalamak: her islem stop yiyorsa, R hesabi tutmuyorsa,
    cikis hic tetiklenmiyorsa burada saatler icinde gorunur
  - parametrelerin GORECELI etkisini olcmek (asagida)

"Kendini gelistirme" ne demek
-----------------------------
Sihir yok. Her islem kucuk bir parametre izgarasindan bir varyantla
aciliyor ve sonuc o varyantin hanesine yaziliyor. Panel hangi varyantin
onde oldugunu GUVEN ARALIGIYLA gosteriyor. Motor kendi kurallarini
otomatik degistirmiyor — degistirseydi kendi gurultusune uyum saglar ve
sana ogrenme gibi gorunen bir asiri uyum uretirdi. Karar senin.

Para
----
Bu motor `trades` tablosuna DOKUNMAZ. Margin, kaldirac, risk butcesi yok;
bildirim gondermez. Yalnizca research_trades'e source='test1h' yazar.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import Counter
from typing import Any, Dict, List, Optional

import numpy as np

from .. import db, runtime
from ..strategies import carry_tsmom as strat
from . import binance, research_metrics

log = logging.getLogger("vortex.test1h")

SOURCE = "test1h"
INTERVAL = "1h"
BARS = 400                    # ~16 gun 1h veri; MIN_BARS 191 rahat siginir
MS_HOUR = 3_600_000

# Parametre izgarasi. Kucuk tutuldu: 6 varyant, her biri esit sirayla
# deneniyor. Buyutmek her varyantin ornek sayisini seyreltir ve hicbiri
# anlamli hale gelmez — az varyant, cok gozlem.
VARIANTS: List[Dict[str, Any]] = [
    {"ad": "t0.5/atr1.5", "min_abs_t": 0.5, "stop_atr_mult": 1.5},
    {"ad": "t0.5/atr2.5", "min_abs_t": 0.5, "stop_atr_mult": 2.5},
    {"ad": "t1.0/atr1.5", "min_abs_t": 1.0, "stop_atr_mult": 1.5},
    {"ad": "t1.0/atr2.5", "min_abs_t": 1.0, "stop_atr_mult": 2.5},
    {"ad": "t1.5/atr1.5", "min_abs_t": 1.5, "stop_atr_mult": 1.5},
    {"ad": "t1.5/atr2.5", "min_abs_t": 1.5, "stop_atr_mult": 2.5},
]

_task: Optional[asyncio.Task] = None
_state: Dict[str, Any] = {
    "running": False, "last_cycle_at": None, "last_error": None,
    "last_opened": 0, "last_closed": 0, "last_scanned": 0, "cycles": 0,
}


# --------------------------------------------------------------------------- #
# Ayar
# --------------------------------------------------------------------------- #
def enabled() -> bool:
    return bool(db.get_setting("test_engine_enabled", False))


def set_enabled(value: bool) -> None:
    db.set_setting("test_engine_enabled", bool(value))


def _cfg() -> Dict[str, Any]:
    return runtime.get()["test_engine"]


def _next_variant() -> Dict[str, Any]:
    """Sirayla dolas. Rastgele secmek kisa vadede varyantlari dengesiz
    ornekler ve karsilastirmayi bozardi."""
    i = int(db.get_setting("test_engine_variant_i", 0) or 0)
    db.set_setting("test_engine_variant_i", (i + 1) % len(VARIANTS))
    return VARIANTS[i % len(VARIANTS)]


# --------------------------------------------------------------------------- #
# Veri
# --------------------------------------------------------------------------- #
async def _bars(symbol: str) -> List[list]:
    rows = await binance.klines(symbol, INTERVAL, BARS)
    # Kapanmamis son bar disari: ana motordaki ile ayni kural. Kapanmamis
    # bari sinyale katmak, backtest'te asla goremeyecegin bir davranistir.
    return rows[:-1] if rows else []


async def _universe(size: int, min_vol_m: float) -> List[str]:
    tickers = await binance.ticker_24h()
    liquid = [t for t in tickers
              if str(t.get("symbol", "")).endswith("USDT")
              and float(t.get("quoteVolume", 0) or 0) >= min_vol_m * 1e6]
    liquid.sort(key=lambda t: -float(t.get("quoteVolume", 0) or 0))
    return [t["symbol"] for t in liquid[:size]]


# --------------------------------------------------------------------------- #
# Kapanis
# --------------------------------------------------------------------------- #
async def close_due() -> int:
    """Suresi dolan test pozisyonlarini kapatir.

    Cikis kurali ana motorunkiyle AYNI mantikta: tutulan saatin barinda
    stop veya hedefe deginildiyse orada kapanir, deginilmediyse barin
    kapanisinda. Ayni barda ikisi de -> STOP (iyimser saymamak icin).
    """
    rows = db.query(
        "SELECT * FROM research_trades WHERE source=? AND status='open'", (SOURCE,))
    if not rows:
        return 0
    if binance.status().get("mode") != "live":
        return 0
    now = db.now_ms()
    closed = 0
    for r in rows:
        row = dict(r)
        if now < row["expires_at"]:
            continue
        symbol = row["symbol"]
        try:
            bars = await binance.klines(symbol, INTERVAL, 6)
            if binance.status().get("mode") != "live":
                return closed
        except Exception as exc:  # noqa: BLE001
            log.warning("test cikis: %s mumlari alinamadi: %s", symbol, exc)
            continue
        bars = [b for b in bars if int(b[0]) >= row["created_at"] and int(b[6]) <= now]
        direction = 1 if row["side"] == "LONG" else -1
        risk = abs(float(row["entry"]) - float(row["stop"]))
        if risk <= 0:
            db.execute("UPDATE research_trades SET status='closed',closed_at=?,exit_reason=? WHERE id=?",
                       (now, "gecersiz_risk", row["id"]))
            continue

        hit = exit_price = None
        mfe = mae = 0.0
        for b in bars:
            high, low = float(b[2]), float(b[3])
            best = ((high - row["entry"]) if direction > 0 else (row["entry"] - low)) / risk
            worst = ((low - row["entry"]) if direction > 0 else (row["entry"] - high)) / risk
            mfe, mae = max(mfe, best), min(mae, worst)
            stop_touch = low <= row["stop"] if direction > 0 else high >= row["stop"]
            target_touch = high >= row["target"] if direction > 0 else low <= row["target"]
            if stop_touch:
                hit, exit_price = "stop", row["stop"]
                break
            if target_touch:
                hit, exit_price = "target", row["target"]
                break
        if hit is None:
            if bars:
                exit_price = float(bars[-1][4])
            else:
                try:
                    tick = await binance.ticker_24h(symbol)
                    if binance.status().get("mode") != "live":
                        return closed
                    exit_price = float(tick["lastPrice"])
                except Exception:  # noqa: BLE001
                    continue
            hit = "sure"

        result_r = round((exit_price - row["entry"]) * direction / risk, 3)
        net = research_metrics.close_values(row, result_r, now)
        db.execute(
            "UPDATE research_trades SET status='closed',closed_at=?,exit_price=?,result_r=?,"
            "gross_result_r=?,cost_r=?,net_result_r=?,outcome=?,exit_reason=?,"
            "last_checked_at=?,mfe_r=?,mae_r=? WHERE id=?",
            (now, exit_price, result_r, net["gross_r"], net["cost_r"], net["net_r"],
             "win" if net["net_r"] > 0 else "loss" if net["net_r"] < 0 else "flat",
             hit, now, round(mfe, 3), round(mae, 3), row["id"]))
        closed += 1
    return closed


# --------------------------------------------------------------------------- #
# Acilis
# --------------------------------------------------------------------------- #
async def open_one() -> Optional[Dict[str, Any]]:
    """Bir varyantla tek test pozisyonu acar. Bulamazsa None."""
    cfg = _cfg()
    if binance.status().get("mode") != "live":
        return None
    var = _next_variant()
    universe = await _universe(int(cfg["universe_size"]), float(cfg["min_quote_volume_m"]))
    if binance.status().get("mode") != "live":
        return None
    if not universe:
        return None

    acik = {r["symbol"] for r in db.query(
        "SELECT symbol FROM research_trades WHERE source=? AND status='open'", (SOURCE,))}
    aday: List[Dict[str, Any]] = []
    sem = asyncio.Semaphore(4)

    async def bak(symbol: str) -> None:
        if symbol in acik:
            return
        async with sem:
            try:
                rows = await _bars(symbol)
                if binance.status().get("mode") != "live":
                    return
                if len(rows) < strat.MIN_BARS:
                    return
                closes = np.array([float(b[4]) for b in rows], dtype=float)
                mom = await asyncio.to_thread(strat.momentum_t, closes)
                if not mom:
                    return
                atr = await asyncio.to_thread(strat.daily_atr, rows, 14)
                if not atr or atr <= 0:
                    return
            except Exception:  # noqa: BLE001
                return
            t = float(mom["t"])
            if abs(t) < float(var["min_abs_t"]):
                return
            aday.append({"symbol": symbol, "t": t, "atr": atr,
                         "price": float(rows[-1][4]), "per": mom.get("per_lookback")})

    await asyncio.gather(*(bak(s) for s in universe))
    if binance.status().get("mode") != "live":
        return None
    _state["last_scanned"] = len(universe)
    if not aday:
        return None

    aday.sort(key=lambda d: -abs(d["t"]))
    best = aday[0]
    side = "LONG" if best["t"] > 0 else "SHORT"
    direction = 1 if side == "LONG" else -1
    price = best["price"]
    stop_dist = best["atr"] * float(var["stop_atr_mult"])
    if stop_dist <= 0:
        return None
    stop = price - direction * stop_dist
    target = price + direction * stop_dist * float(cfg["target_r"])
    now = db.now_ms()
    expires = now + int(float(cfg["hold_minutes"]) * 60_000)

    cost_basis = price / stop_dist
    meta = {"variant": var["ad"], "min_abs_t": var["min_abs_t"],
            "stop_atr_mult": var["stop_atr_mult"], "t_stat": round(best["t"], 3),
            "atr": round(best["atr"], 8), "hold_minutes": float(cfg["hold_minutes"]),
            "per_lookback": best.get("per"),
            "cost_basis": round(cost_basis, 5),
            "fee_cost_r": round(research_metrics.FALLBACK_ROUNDTRIP_PCT * cost_basis, 6),
            "funding_cost_r": 0.0,
            "horizon_days": max(float(cfg["hold_minutes"]) / 1440.0, 1 / 24)}
    rid = db.execute(
        "INSERT INTO research_trades(symbol,interval,side,score,entry,stop,target,reasons,"
        "status,created_at,expires_at,source,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (best["symbol"], INTERVAL, side, 0, price, stop, target,
         json.dumps([f"test1h {var['ad']} t={best['t']:+.2f}"], ensure_ascii=False),
         "open", now, expires, SOURCE, json.dumps(meta, ensure_ascii=False)))
    log.info("test1h ACILDI %s %s t=%+.2f varyant=%s", best["symbol"], side, best["t"], var["ad"])
    return {"id": rid, "symbol": best["symbol"], "side": side,
            "t": round(best["t"], 3), "variant": var["ad"]}


# --------------------------------------------------------------------------- #
# Dongu
# --------------------------------------------------------------------------- #
async def cycle_once() -> Dict[str, Any]:
    started = time.monotonic()
    try:
        if binance.status().get("mode") != "live":
            _state["last_error"] = "Canlı Binance verisi bekleniyor; test döngüsü duraklatıldı"
            return {"ok": True, "skipped": "canlı veri bekleniyor"}
        closed = await close_due()
        opened = 0
        cfg = _cfg()
        # Bosta kalmasin: es zamanli hedefe kadar acmayi dener.
        hedef = int(cfg["concurrent"])
        acik = db.query_one(
            "SELECT COUNT(*) c FROM research_trades WHERE source=? AND status='open'", (SOURCE,))
        acik_n = int(acik["c"]) if acik else 0
        for _ in range(max(0, hedef - acik_n)):
            got = await open_one()
            if not got:
                break
            opened += 1
        _state.update(last_cycle_at=db.now_ms(), last_closed=closed, last_opened=opened,
                      last_error=None, cycles=_state["cycles"] + 1)
        log.info("test1h dongu: kapanan=%d acilan=%d (%.1fs)",
                 closed, opened, time.monotonic() - started)
        return {"ok": True, "closed": closed, "opened": opened}
    except Exception as exc:  # noqa: BLE001
        _state["last_error"] = f"{type(exc).__name__}: {exc}"
        log.exception("test1h dongusu hata verdi")
        return {"ok": False, "error": _state["last_error"]}


async def _loop() -> None:
    await asyncio.sleep(70)          # acilista digerlerine yer ver
    while True:
        try:
            cfg = _cfg()
            if enabled() and cfg.get("enabled", True):
                await cycle_once()
            await asyncio.sleep(max(300, int(cfg["cycle_seconds"])))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("test1h dongusu kirildi, 5 dk sonra tekrar")
            await asyncio.sleep(300)


async def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())
        _state["running"] = True
        log.info("Hizli test motoru baslatildi (1 saatlik dongu, yalnizca olcum)")


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


# --------------------------------------------------------------------------- #
# Karne
# --------------------------------------------------------------------------- #
def _ozet(rs: List[float]) -> Dict[str, Any]:
    n = len(rs)
    if not n:
        return {"n": 0}
    mean = sum(rs) / n
    sd = (sum((x - mean) ** 2 for x in rs) / (n - 1)) ** .5 if n > 1 else 0.0
    se = sd / math.sqrt(n) if n else 0.0
    wins = [x for x in rs if x > 0]
    # Tek gozlemde standart hata 0 cikiyor ve guven araligi tek noktaya
    # cokuyordu; kod da bunu "anlamli" sayiyordu. Bir islem hicbir seyi
    # kanitlamaz — anlamlilik icin en az 5 gozlem ve sifir olmayan
    # dagilim sart.
    guvenilir = n >= 5 and se > 0
    return {
        "n": n,
        "avg_r": round(mean, 4),
        "total_r": round(sum(rs), 2),
        "win_rate": round(len(wins) / n * 100, 1),
        "ci95": ([round(mean - 1.96 * se, 4), round(mean + 1.96 * se, 4)]
                 if guvenilir else None),
        "significant": bool(guvenilir and ((mean - 1.96 * se) > 0 or (mean + 1.96 * se) < 0)),
    }


def scorecard() -> Dict[str, Any]:
    rows = db.query(
        "SELECT * FROM research_trades WHERE source=? AND status='closed' "
        "AND result_r IS NOT NULL ORDER BY closed_at", (SOURCE,))
    rs = research_metrics.values(rows)
    out: Dict[str, Any] = {"overall": _ozet(rs),
                           "exits": dict(Counter((r["exit_reason"] or "?") for r in rows))}

    # Varyant kirilimi — "kendini gelistirme" burada. Motor kurallari
    # kendi degistirmiyor; hangi varyantin onde oldugunu GOSTERIYOR.
    per: Dict[str, List[float]] = {}
    for r in rows:
        try:
            m = json.loads(r["meta"] or "{}")
        except (TypeError, ValueError):
            m = {}
        net = (float(r["net_result_r"]) if r["net_result_r"] is not None
               else float(research_metrics.breakdown(r)["net_r"]))
        per.setdefault(m.get("variant") or "?", []).append(net)
    varyantlar = [{"variant": k, **_ozet(v)} for k, v in per.items()]
    varyantlar.sort(key=lambda d: -(d.get("avg_r") or -99))
    out["variants"] = varyantlar

    orow = db.query_one(
        "SELECT COUNT(*) c FROM research_trades WHERE source=? AND status='open'", (SOURCE,))
    out["open"] = int(orow["c"]) if orow else 0
    if rs:
        # Bu buyuklukteki bir farki kanitlamak icin gereken islem sayisi.
        # "az veri" demek yerine KAC gerektigini soylemek daha durust.
        mean = sum(rs) / len(rs)
        sd = (sum((x - mean) ** 2 for x in rs) / (len(rs) - 1)) ** .5 if len(rs) > 1 else 0
        out["n_needed"] = int((2 * sd / abs(mean)) ** 2) if mean and sd else None
    return out


def open_rows() -> List[Dict[str, Any]]:
    out = []
    for r in db.query(
            "SELECT * FROM research_trades WHERE source=? AND status='open' "
            "ORDER BY created_at DESC", (SOURCE,)):
        d = dict(r)
        try:
            d["meta"] = json.loads(d.get("meta") or "{}")
        except (TypeError, ValueError):
            d["meta"] = {}
        out.append(d)
    return out


def recent(limit: int = 25) -> List[Dict[str, Any]]:
    out = []
    for r in db.query(
            "SELECT * FROM research_trades WHERE source=? ORDER BY created_at DESC LIMIT ?",
            (SOURCE, limit)):
        d = dict(r)
        try:
            d["meta"] = json.loads(d.get("meta") or "{}")
        except (TypeError, ValueError):
            d["meta"] = {}
        out.append(d)
    return out


def status() -> Dict[str, Any]:
    cfg = _cfg()
    return {
        **_state,
        "enabled": enabled(),
        "config": cfg,
        "variants": [v["ad"] for v in VARIANTS],
        "scorecard": scorecard(),
        "open_rows": open_rows(),
        "recent": recent(25),
    }
