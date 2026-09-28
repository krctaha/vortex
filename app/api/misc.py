from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import runtime
from ..deps import check_origin, current_user
from ..services import binance, binance_ws, coin_icons, market_extra, telegram

router = APIRouter(prefix="/api", tags=["misc"])


@router.get("/system/status")
async def system_status(_user=Depends(current_user)):
    st = binance.status()
    return {
        "data_mode": st["mode"],
        "reason": st.get("reason", ""),
        "used_weight": st.get("used_weight"),
        # Demo'ya dusuldugunde sistem arka planda tekrar baglanmayi deniyor.
        # Bunu disari vermek onemli: kullanici "demo" gorup "sistem oldu" diye
        # dusunmesin, kacinci denemede oldugunu gorsun.
        "recover_tries": st.get("recover_tries", 0),
        "recovered_at": st.get("recovered_at"),
        "ws": binance_ws.status(),
        "telegram_configured": telegram.configured(_user.get("telegram_token", ""), _user.get("telegram_chat_id", "")),
        "risk_frame": runtime.get()["risk"],
    }


@router.get("/news")
async def news(limit: int = 20, _user=Depends(current_user)):
    return await market_extra.news(limit)


@router.get("/calendar")
async def calendar(only_important: bool = False, _user=Depends(current_user)):
    return await market_extra.economic_calendar(only_important)


class TelegramBody(BaseModel):
    text: str
    silent: bool = False


@router.post("/telegram/send")
async def telegram_send(body: TelegramBody, user=Depends(current_user),
                        _: None = Depends(check_origin)):
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Boş mesaj")
    result = await telegram.send(body.text, token=user["telegram_token"],
                                 chat_id=user["telegram_chat_id"], silent=body.silent)
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result.get("error") or "Gönderilemedi")
    return result


@router.post("/telegram/test")
async def telegram_test(user=Depends(current_user), _: None = Depends(check_origin)):
    result = await telegram.test_connection(user["telegram_token"], user["telegram_chat_id"])
    if not result.get("ok"):
        raise HTTPException(502, result.get("error") or "Bot doğrulanamadı")
    sent = await telegram.send("<b>VORTEX · Bağlantı testi</b>\nBot ve hedef sohbet bağlantısı çalışıyor.\nAraştırma bildirimleri; gerçek emir gönderilmez.",
                               token=user["telegram_token"], chat_id=user["telegram_chat_id"])
    if not sent.get("ok"):
        raise HTTPException(502, sent.get("error") or "Hedef sohbete mesaj gönderilemedi")
    return {**result, "delivered": True}


@router.get("/icons/status")
async def icons_status(_user=Depends(current_user)):
    return coin_icons.cache_stats()


@router.post("/icons/refresh")
async def icons_refresh(_user=Depends(current_user), _: None = Depends(check_origin)):
    """Ikon onbellegini CoinGecko'dan tazeler.

    Elle tetiklenir; sunucu her acilista disariya istek atmasin diye
    otomatik calismiyor. Ikonlar degismedigi surece bir kez yeter.
    """
    try:
        symbols = [s["symbol"] for s in await binance.perpetual_symbols()]
    except Exception:  # noqa: BLE001
        symbols = None      # evren alinamadiysa ilk 750 coini indir
    return await coin_icons.refresh(symbols)
