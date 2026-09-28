"""Canli Futures baglantisi ve ana emniyet anahtari — yalnizca yonetici."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import admin_user, check_origin
from ..services import binance_trade, live_trade

router = APIRouter(prefix="/api/trading", tags=["trading"])


@router.get("/status")
async def status(_user=Depends(admin_user)):
    live = await live_trade.status()
    return {
        "has_keys": binance_trade.has_keys(),
        "hint": binance_trade.key_hint(),
        "order_layer": True,
        "live": live,
        "account": await binance_trade.account_summary(),
    }


class KeyBody(BaseModel):
    api_key: str = Field(min_length=16, max_length=256)
    api_secret: str = Field(min_length=16, max_length=256)


@router.post("/keys")
def save_keys(body: KeyBody, _user=Depends(admin_user), _: None = Depends(check_origin)):
    """Anahtari sifreleyerek kaydeder. Geri OKUNMAZ — yalnizca son 4 hane."""
    key, secret = body.api_key.strip(), body.api_secret.strip()
    if key == secret:
        raise HTTPException(status_code=400, detail="Key ve Secret aynı olamaz")
    if " " in key or " " in secret:
        raise HTTPException(status_code=400,
                            detail="Anahtarda boşluk var — kopyalarken fazladan karakter almış olabilirsin")
    binance_trade.save_keys(key, secret)
    return {"ok": True, "hint": binance_trade.key_hint()}


@router.delete("/keys")
def delete_keys(_user=Depends(admin_user), _: None = Depends(check_origin)):
    if live_trade.enabled() or live_trade.open_rows():
        raise HTTPException(status_code=409,
                            detail="Canlı yürütme veya izlenen canlı pozisyon varken anahtar silinemez")
    binance_trade.clear_keys()
    return {"ok": True}


@router.post("/test")
async def test(_user=Depends(admin_user), _: None = Depends(check_origin)):
    """Baglantiyi dogrular; test endpointi gercek pozisyon olusturmaz."""
    base = await binance_trade.test_connection()
    live = await live_trade.preflight(include_order_test=True)
    base["live_preflight"] = live
    base["ok"] = bool(base.get("ok") and live.get("ok"))
    return base


class LiveToggleBody(BaseModel):
    enabled: bool
    confirmation: str = ""


@router.post("/live/toggle")
async def live_toggle(body: LiveToggleBody, _user=Depends(admin_user),
                      _: None = Depends(check_origin)):
    if body.enabled:
        if body.confirmation.strip().upper() != "CANLI":
            raise HTTPException(status_code=400, detail="Onay alanına CANLI yazılmalı")
        check = await live_trade.preflight(include_order_test=True)
        if not check.get("ok"):
            raise HTTPException(status_code=409, detail={
                "message": "Canlı yürütme güvenlik kontrolünü geçmedi",
                "checks": check.get("checks", []),
            })
        live_trade.set_enabled(True)
    else:
        # Anahtari kapatmak yeni GIRISLERI durdurur. Acik pozisyonlarin stopu
        # ve motor cikis/mutabakat takibi calismaya devam eder.
        live_trade.set_enabled(False)
    return {"ok": True, "live": await live_trade.status()}
