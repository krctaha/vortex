from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from ..deps import current_user
from ..services import analysis, binance, binance_ws, market_extra

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
TERMINAL_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT"]


async def _hero(symbol: str) -> Dict[str, Any]:
    try:
        # 24 saatlik mini seri: kartta fiyatin YANINDA degil, ARKASINDA
        # duracak. Rakam "ne kadar" der, cizgi "nasil geldi" der; ikisi
        # ayni yeri kaplamadan farkli sorulari cevapliyor.
        t, bars = await asyncio.gather(
            binance.ticker_24h(symbol),
            binance.klines(symbol, "1h", 24),
        )
        spark = []
        for b in (bars or []):
            try:
                spark.append(round(float(b[4]), 8))
            except (TypeError, ValueError, IndexError):
                continue
        live = binance_ws.get_price(symbol)
        return {
            "symbol": symbol,
            "price": live or float(t["lastPrice"]),
            "rest_price": float(t["lastPrice"]),
            "change_pct": float(t["priceChangePercent"]),
            "high": float(t["highPrice"]),
            "low": float(t["lowPrice"]),
            "quote_volume": float(t["quoteVolume"]),
            "spark": spark,
            "ok": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {"symbol": symbol, "ok": False, "error": str(exc)[:120]}


def _technical_score(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Şeffaf 0-100 piyasa uyum puanı; emir skoru değildir.

    Quantify benzeri tek bakışta yön özeti gerekir, fakat kapalı/proprietary
    bir sayıyı taklit etmiyoruz. Altı görünür teknik koşul eşit ağırlıklıdır.
    """
    ind = snap.get("indicators") or {}
    bias = (snap.get("structure") or {}).get("bias")
    price = float(snap.get("price") or 0)

    def side(condition_up: bool, condition_down: bool) -> int:
        return 1 if condition_up else -1 if condition_down else 0

    rsi = float(ind.get("rsi14") or 50)
    ema20 = float(ind.get("ema20") or 0)
    ema50 = float(ind.get("ema50") or 0)
    votes = {
        "structure": side(bias == "bull", bias == "bear"),
        "price_ema20": side(bool(ema20 and price > ema20), bool(ema20 and price < ema20)),
        "ema_trend": side(bool(ema20 and ema50 and ema20 > ema50), bool(ema20 and ema50 and ema20 < ema50)),
        "supertrend": side(ind.get("supertrend_dir") == 1, ind.get("supertrend_dir") == -1),
        "macd": side(float(ind.get("macd_histogram") or 0) > 0, float(ind.get("macd_histogram") or 0) < 0),
        "rsi": side(rsi > 55, rsi < 45),
    }
    score = round(50 + (sum(votes.values()) / len(votes)) * 50, 1)
    if score >= 75:
        label, direction = "Güçlü yukarı", "bull"
    elif score >= 58:
        label, direction = "Yukarı eğilim", "bull"
    elif score > 42:
        label, direction = "Kararsız", "neutral"
    elif score > 25:
        label, direction = "Aşağı eğilim", "bear"
    else:
        label, direction = "Güçlü aşağı", "bear"
    return {"value": score, "label": label, "direction": direction,
            "aligned": max(sum(v > 0 for v in votes.values()), sum(v < 0 for v in votes.values())),
            "components": votes}


async def _market_card(symbol: str) -> Dict[str, Any]:
    try:
        hero, snap = await asyncio.gather(_hero(symbol), analysis.snapshot(symbol, "4h", 300))
    except Exception as exc:  # noqa: BLE001
        return {"symbol": symbol, "ok": False, "technical": None, "error": str(exc)[:120]}
    if not hero.get("ok") or not snap.get("ok"):
        return {**hero, "technical": None}
    ind = snap.get("indicators") or {}
    nearest = (snap.get("levels") or {}).get("nearest") or {}
    support = (nearest.get("support") or {}).get("price")
    resistance = (nearest.get("resistance") or {}).get("price")
    return {
        **hero,
        "technical": _technical_score(snap),
        "rsi": ind.get("rsi14"),
        "adx": ind.get("adx14"),
        "regime": snap.get("regime"),
        "bias": (snap.get("structure") or {}).get("bias"),
        "support": support,
        "resistance": resistance,
        "interval": "4h",
    }


@router.get("/overview")
async def overview(user=Depends(current_user)):
    binance_ws.watch(TERMINAL_SYMBOLS)
    heroes, fng, glob, gold, btc_ctx = await asyncio.gather(
        asyncio.gather(*(_market_card(s) for s in TERMINAL_SYMBOLS)),
        market_extra.fear_greed(),
        market_extra.global_market(),
        market_extra.gold_spot(),
        analysis.market_context("BTCUSDT"),
    )
    btc_card = next((x for x in heroes if x.get("symbol") == "BTCUSDT"), {})
    return {
        "user": {"display_name": user["display_name"] or user["username"],
                 "username": user["username"], "role": user["role"]},
        "heroes": list(heroes),
        "fear_greed": fng,
        "global": glob,
        "gold_spot": gold,
        "btc": {
            "regime": btc_card.get("regime"),
            "bias": btc_card.get("bias"),
            "rsi": btc_card.get("rsi"),
            "interval": "4h",
        },
        "derivatives": btc_ctx,
        "system": {"data_mode": binance.status()["mode"],
                   "reason": binance.status().get("reason", ""),
                   "ws": binance_ws.status()},
    }
