from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import admin_user, check_origin, current_user
from ..services import copy_trade, live_trade, premium_engine, test_engine, tsmom_engine, validation, smc_market, smc_board, smc_rsi, binance_ws
from .. import db

router = APIRouter(prefix="/api/engine", tags=["engine"])


class ToggleBody(BaseModel):
    enabled: bool


class PremiumOrderBody(BaseModel):
    candidate: dict
    confirmation: str = ""


@router.get("/premium/status")
@router.get("/smc/status")
def premium_status(_user=Depends(current_user)):
    return premium_engine.status()


@router.get("/smc/dashboard")
async def smc_dashboard(_user=Depends(current_user)):
    try:
        scanner = premium_engine.status()
        scanner["results"] = [{**{k: r.get(k) for k in ("symbol", "phase", "side", "ready", "sparkline", "ok", "demo", "score")},
                               "verification": {"score": (r.get("verification") or {}).get("score")}}
                              for r in scanner.get("results", [])]
        market=await smc_market.dashboard()
        radar=smc_rsi.radar([r['symbol'] for r in market['markets']],binance_ws.snapshot(),db.now_ms())
        return {**market, "scanner": scanner, "board":smc_board.snapshot(scanner), "rsi_radar":radar}
    except Exception:
        raise HTTPException(status_code=503, detail="Canlı piyasa verisine erişilemiyor. Sentetik veri kullanılmadı.")


@router.get("/smc/chart")
async def smc_chart(symbol: str = "BTCUSDT", _user=Depends(current_user)):
    markets = await smc_market.universe()
    if symbol not in {r["symbol"] for r in markets}:
        raise HTTPException(status_code=422, detail="İzlenen 200 piyasadan bir sembol seçin.")
    try:
        # Separate short cache: chart includes the forming candle, scanner does not.
        if smc_market.demo_mode():
            rows = await smc_market.binance.klines(symbol, "15m", 100)
        else:
            rows = await smc_market.cache.wrap(f"smc:chart:{symbol}", 10,
                lambda: smc_market.request("/fapi/v1/klines", {"symbol": symbol, "interval": "15m", "limit": 100}, 2))
        return {"symbol": symbol, "rows": rows, "demo": smc_market.demo_mode()}
    except Exception:
        raise HTTPException(status_code=503, detail="Grafik verisi alınamadı.")


@router.get("/smc/history")
def smc_history(_user=Depends(current_user)):
    return premium_engine.history()


@router.post("/premium/toggle")
def premium_toggle(body: ToggleBody, _user=Depends(admin_user),
                   _: None = Depends(check_origin)):
    premium_engine.set_enabled(body.enabled)
    return {"ok": True, **premium_engine.status()}


@router.post("/premium/scan-now")
@router.post("/smc/scan-now")
async def premium_scan(_user=Depends(current_user), _: None = Depends(check_origin)):
    return await premium_engine.scan_once(user_id=_user.get("id"))


@router.post("/premium/open-live")
async def premium_open_live(body: PremiumOrderBody, _user=Depends(admin_user),
                            _: None = Depends(check_origin)):
    return await premium_engine.open_live(body.candidate, int(_user["id"]), body.confirmation)


@router.get("/status")
def engine_status(_user=Depends(admin_user)):
    return {"enabled": False, "retired": True, "replacement": "tsmom",
            "message": "Eski gösterge skoru motoru kaldırıldı; tek karar motoru TSMOM."}


@router.get("/control-center")
async def control_center(_user=Depends(admin_user)):
    """Tek motorun veri, karar, risk, yürütme ve kanıt katmanlarını raporla."""
    data = validation.overview()
    try:
        live = await live_trade.status()
    except Exception as exc:  # hesap ozeti paneli ana motor durumunu dusurmesin
        live = {"enabled": live_trade.enabled(), "open_live": len(live_trade.open_rows()),
                "protected": 0, "account": {"connected": False},
                "error": str(exc)[:180]}
    data["execution"] = {"copy": copy_trade.status(), "live": live}
    data["runtime"] = tsmom_engine.status()
    data["legacy"] = {"retired": True, "new_signals": False,
                      "history_preserved": True}
    return data


@router.post("/toggle")
async def engine_toggle(body: ToggleBody, _user=Depends(admin_user),
                        _: None = Depends(check_origin)):
    raise HTTPException(status_code=410,
                        detail="Eski skor motoru kaldırıldı. Ana motor: TSMOM.")


@router.post("/scan-now")
async def scan_now(_user=Depends(admin_user), _: None = Depends(check_origin)):
    raise HTTPException(status_code=410,
                        detail="Eski skor motoru kaldırıldı. /tsmom/scan-now kullanılır.")


# --------------------------------------------------------------------------- #
# TSMOM + carry — sistemin tek karar motoru
# --------------------------------------------------------------------------- #
@router.get("/tsmom/status")
def tsmom_status(_user=Depends(admin_user)):
    return tsmom_engine.status()


class CloseBody(BaseModel):
    id: Optional[int] = None
    all: bool = False


@router.post("/tsmom/close")
async def tsmom_close(body: CloseBody, _user=Depends(admin_user),
                      _: None = Depends(check_origin)):
    """Acik kaydi elle kapat. id verilirse tek, all=true ise hepsi."""
    if body.all:
        return await tsmom_engine.close_all_manual()
    if not body.id:
        return {"ok": False, "error": "id ya da all gerekli"}
    return await tsmom_engine.close_manual(int(body.id))


@router.get("/tsmom/pulse")
def tsmom_pulse(_user=Depends(current_user)):
    """Kenar cubugu ve ana panel karnesi icin hafif ozet.

    admin_user degil current_user: bu uc yalnizca OKUYOR ve her sayfa
    yuklenisinde cagriliyor; yonetici kisitlamasi buraya gerekmez.
    """
    return tsmom_engine.pulse()


# --------------------------------------------------------------------------- #
# Hizli test motoru — 1 saatlik dongu, yalnizca olcum
# --------------------------------------------------------------------------- #
@router.get("/test/status")
def test_status(_user=Depends(admin_user)):
    return test_engine.status()


@router.post("/test/toggle")
async def test_toggle(body: ToggleBody, _user=Depends(admin_user),
                      _: None = Depends(check_origin)):
    test_engine.set_enabled(body.enabled)
    if body.enabled:
        asyncio.create_task(test_engine.cycle_once())
    return {"ok": True, **test_engine.status()}


@router.post("/test/cycle-now")
async def test_cycle(_user=Depends(admin_user), _: None = Depends(check_origin)):
    return await test_engine.cycle_once()


# --------------------------------------------------------------------------- #
# Copy Trade — para katmani
# --------------------------------------------------------------------------- #
@router.get("/copy/status")
def copy_status(_user=Depends(admin_user)):
    return copy_trade.status()


@router.post("/copy/toggle")
def copy_toggle(body: ToggleBody, _user=Depends(admin_user),
                _: None = Depends(check_origin)):
    copy_trade.set_enabled(body.enabled)
    return {"ok": True, **copy_trade.status()}


@router.get("/tsmom/recent")
def tsmom_recent(limit: int = 20, _user=Depends(admin_user)):
    return {"items": tsmom_engine.recent(limit)}


@router.post("/tsmom/toggle")
async def tsmom_toggle(body: ToggleBody, _user=Depends(admin_user),
                       _: None = Depends(check_origin)):
    if body.enabled:
        from .. import db
        if db.get_setting("primary_engine", "smc_ict") != "tsmom":
            raise HTTPException(status_code=409, detail="Ana motor SMC/ICT. TSMOM yeni girişleri arşiv modunda.")
    tsmom_engine.set_enabled(body.enabled)
    if body.enabled:
        asyncio.create_task(tsmom_engine.scan_once())
    return {"ok": True, **tsmom_engine.status()}


@router.get("/tsmom/scorecard")
def tsmom_scorecard(days: int = 90, _user=Depends(admin_user)):
    return tsmom_engine.scorecard(days)


@router.post("/tsmom/selftest")
async def tsmom_selftest(days: int = 120, symbols: int = 12,
                         _user=Depends(admin_user), _: None = Depends(check_origin)):
    return await tsmom_engine.run_selftest(days, symbols)


@router.post("/tsmom/scan-now")
async def tsmom_scan(_user=Depends(admin_user), _: None = Depends(check_origin)):
    return await tsmom_engine.scan_once()
