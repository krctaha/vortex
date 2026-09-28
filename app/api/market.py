from __future__ import annotations

import asyncio
import math
import statistics
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import settings
from ..deps import current_user
from ..indicators import core
from ..services import analysis, binance, binance_ws, market_extra, rsi_context, smc_ict_engine
from ..services.cache import cache

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/symbols")
async def symbols(q: str = "", limit: int = 300, _user=Depends(current_user)):
    rows = await binance.perpetual_symbols()
    if q:
        needle = q.upper()
        rows = [r for r in rows if needle in r["symbol"]]
    return {"count": len(rows), "symbols": rows[:limit], "demo": binance.is_demo()}


@router.get("/klines")
async def klines(symbol: str, interval: str = "15m", limit: int = 500, _user=Depends(current_user)):
    rows = await binance.klines(symbol.upper(), interval, limit)
    return {
        "symbol": symbol.upper(), "interval": interval, "demo": binance.is_demo(),
        "candles": [{"time": int(r[0]) // 1000, "open": float(r[1]), "high": float(r[2]),
                     "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
                    for r in rows],
    }


@router.get("/snapshot")
async def snapshot(symbol: str, interval: str = "15m", limit: int = 500,
                   series: bool = False, _user=Depends(current_user)):
    binance_ws.watch([symbol.upper()])
    snap = await analysis.snapshot(symbol.upper(), interval, limit, include_series=series)
    if not snap.get("ok"):
        raise HTTPException(status_code=422, detail=snap.get("error", "analiz başarısız"))
    snap["context"] = await analysis.market_context(symbol.upper())
    snap["detailed_analysis"] = analysis.detailed_analysis(snap)
    snap["analysis_report"] = analysis.telegram_report(snap)
    snap["x_post"] = analysis.x_post(snap)
    return snap


@router.get("/smc")
async def smc_analysis(symbol: str, htf: str = "4h", ltf: str = "15m",
                       _user=Depends(current_user)):
    """Ordered HTF->LTF SMC/ICT analysis; always paper/decision support."""
    allowed = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
    if htf not in allowed or ltf not in allowed:
        raise HTTPException(status_code=422, detail="Geçersiz zaman dilimi")
    from ..services.demo import INTERVAL_MS
    if INTERVAL_MS[htf] <= INTERVAL_MS[ltf]:
        raise HTTPException(status_code=422, detail="Üst zaman dilimi giriş diliminden büyük olmalı")
    if not symbol.isascii() or not symbol.isalnum() or len(symbol) > 24:
        raise HTTPException(status_code=422, detail="Geçersiz sembol")
    result = await smc_ict_engine.analyze_symbol(symbol.upper(), htf, ltf)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("error", "SMC analizi başarısız"))
    return result


@router.get("/ticker")
async def ticker(symbol: Optional[str] = None, _user=Depends(current_user)):
    return {"data": await binance.ticker_24h(symbol.upper() if symbol else None),
            "demo": binance.is_demo()}


@router.get("/movers")
async def movers(limit: int = 8, min_quote_volume: float = 5_000_000, _user=Depends(current_user)):
    """En cok yukselen/dusen. Hacim filtresi SART: yoksa liste likiditesiz
    mikro-cap'lerle dolar."""
    data = await binance.ticker_24h()
    perps = {r["symbol"] for r in await binance.perpetual_symbols()}
    rows = [r for r in data if r["symbol"] in perps
            and float(r.get("quoteVolume", 0) or 0) >= min_quote_volume]
    rows.sort(key=lambda r: float(r["priceChangePercent"]), reverse=True)

    def fmt(r):
        return {"symbol": r["symbol"], "price": float(r["lastPrice"]),
                "change_pct": float(r["priceChangePercent"]),
                "quote_volume": float(r["quoteVolume"])}

    return {"gainers": [fmt(r) for r in rows[:limit]],
            "losers": [fmt(r) for r in rows[-limit:][::-1]],
            "universe": len(rows), "demo": binance.is_demo()}


@router.get("/board")
async def market_board(limit: int = 20, min_quote_volume: float = 5_000_000,
                       _user=Depends(current_user)):
    """Terminaldeki hacim, ısı haritası ve piyasa genişliği tek veri kesiti.

    Bütün modüller aynı anda aynı 24 saatlik Binance fotoğrafını kullanır;
    ayrı isteklerden gelen çelişkili fiyat/değişim değerleri gösterilmez.
    """
    data, symbols = await asyncio.gather(binance.ticker_24h(), binance.perpetual_symbols())
    perps = {r["symbol"] for r in symbols}
    rows = [r for r in data if r.get("symbol") in perps
            and float(r.get("quoteVolume", 0) or 0) >= min_quote_volume]

    def fmt(r):
        return {"symbol": r["symbol"], "price": float(r["lastPrice"]),
                "change_pct": float(r["priceChangePercent"]),
                "quote_volume": float(r["quoteVolume"]),
                "high": float(r.get("highPrice", 0) or 0),
                "low": float(r.get("lowPrice", 0) or 0)}

    by_volume = sorted(rows, key=lambda r: float(r.get("quoteVolume", 0) or 0), reverse=True)
    by_change = sorted(rows, key=lambda r: float(r.get("priceChangePercent", 0) or 0), reverse=True)
    changes = [float(r.get("priceChangePercent", 0) or 0) for r in rows]
    return {
        "top_volume": [fmt(r) for r in by_volume[:max(5, min(limit, 40))]],
        "heatmap": [fmt(r) for r in by_volume[:24]],
        "gainers": [fmt(r) for r in by_change[:10]],
        "losers": [fmt(r) for r in by_change[-10:][::-1]],
        "breadth": {
            "up": sum(v > 0 for v in changes),
            "down": sum(v < 0 for v in changes),
            "flat": sum(v == 0 for v in changes),
            "average_change": round(sum(changes) / len(changes), 2) if changes else None,
        },
        "universe": len(rows), "demo": binance.is_demo(),
    }


def _finite_last(values, default: Optional[float] = None) -> Optional[float]:
    """Bir gösterge dizisinin son kullanılabilir değerini güvenle döndürür."""
    for value in reversed(values):
        number = float(value)
        if math.isfinite(number):
            return number
    return default


def _change(close, bars: int = 1) -> Optional[float]:
    if len(close) <= bars:
        return None
    old = float(close[-1 - bars])
    return round((float(close[-1]) / old - 1.0) * 100.0, 2) if old else None


def _screener_frame(rows: List[list]) -> Dict[str, Any]:
    """Tek zaman dilimini tarayıcı için küçük, karşılaştırılabilir özete çevirir."""
    p = binance.parse_klines(rows)
    close, high, low = p["close"], p["high"], p["low"]
    price = float(close[-1])
    rsi = _finite_last(core.rsi(close, 14), 50.0)
    ema12 = _finite_last(core.ema(close, 12), price)
    ema20 = _finite_last(core.ema(close, 20), price)
    ema26 = _finite_last(core.ema(close, 26), price)
    ema50 = _finite_last(core.ema(close, 50), price)
    atr = _finite_last(core.atr(high, low, close, 14), 0.0)
    _, upper, lower, _ = core.bollinger(close, 20, 2.0)
    bb_hi, bb_lo = _finite_last(upper), _finite_last(lower)
    votes = int(price >= ema20) + int(ema20 >= ema50) + int(ema12 >= ema26)
    return {
        "rsi": round(float(rsi), 2),
        "change_pct": _change(close),
        "trend": "bull" if votes >= 2 else "bear",
        "ema": "bull" if ema20 >= ema50 else "bear",
        "macd": "bull" if ema12 >= ema26 else "bear",
        "bb": "above" if bb_hi is not None and price > bb_hi else
              "below" if bb_lo is not None and price < bb_lo else "inside",
        "atr_pct": round(float(atr) / price * 100.0, 2) if price else None,
        "votes": votes,
    }


def _derivatives_frame(premium: Dict[str, Any], oi_rows: List[Dict[str, Any]],
                      price_rows: List[list]) -> Dict[str, Any]:
    """Funding ve OI'yi tarayici icin olculebilir, kisa bir ozete cevirir."""
    result: Dict[str, Any] = {
        "funding_bp": None, "open_interest_usd": None,
        "oi_change_pct": None, "price_change_pct": None, "oi_context": None,
    }
    try:
        result["funding_bp"] = round(float(premium.get("lastFundingRate", 0) or 0) * 10_000, 2)
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        if len(oi_rows) >= 2:
            first = float(oi_rows[0]["sumOpenInterestValue"])
            last = float(oi_rows[-1]["sumOpenInterestValue"])
            result["open_interest_usd"] = round(last, 2)
            result["oi_change_pct"] = round((last - first) / first * 100, 2) if first else None
            closes = [float(row[4]) for row in price_rows]
            result["price_change_pct"] = round((closes[-1] / closes[0] - 1) * 100, 2) if closes and closes[0] else None
            price_change = result["price_change_pct"] or 0
            oi_change = result["oi_change_pct"] or 0
            if price_change > 0.15 and oi_change > 0.5:
                result["oi_context"] = "Yeni long"
            elif price_change > 0.15 and oi_change < -0.5:
                result["oi_context"] = "Short kapanisi"
            elif price_change < -0.15 and oi_change > 0.5:
                result["oi_context"] = "Yeni short"
            elif price_change < -0.15 and oi_change < -0.5:
                result["oi_context"] = "Long kapanisi"
            else:
                result["oi_context"] = "Belirsiz"
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
        pass
    return result


DAY_MS = 86_400_000


def _volume_tier(quote_volume: float) -> Dict[str, str]:
    """Hacmi yalniz sayi olarak degil, taranabilir bir katman olarak sun."""
    if quote_volume >= 100_000_000:
        return {"key": "high", "label": "Yüksek hacim"}
    if quote_volume >= 10_000_000:
        return {"key": "medium", "label": "Orta hacim"}
    return {"key": "emerging", "label": "Gelişen hacim"}


def _listing_lifecycle(contracts: List[Dict[str, Any]],
                       tickers: List[Dict[str, Any]], now_ms: Optional[int] = None,
                       new_days: int = 45, delist_days: int = 180) -> Dict[str, Any]:
    """exchangeInfo durum/tarihlerinden yeni liste ve delist takvimi.

    SETTLING kayitlari Binance cevabinda aylarca kalabildigi icin status tek
    basina yeterli degildir. Sadece deliveryDate gelecekteyse "delist edilecek"
    sayilir; boylece tarihsel mezarlik kullaniciya guncel alarm diye sunulmaz.
    """
    now = int(now_ms or time.time() * 1000)
    ticker_by_symbol = {t.get("symbol"): t for t in tickers}

    def as_ms(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def item(contract: Dict[str, Any], event_at: int, event: str) -> Dict[str, Any]:
        ticker = ticker_by_symbol.get(contract.get("symbol"), {})
        volume = float(ticker.get("quoteVolume", 0) or 0)
        tier = _volume_tier(volume)
        return {
            "symbol": contract.get("symbol"),
            "base_asset": contract.get("baseAsset"),
            "status": contract.get("status"),
            "event": event,
            "event_at": event_at or None,
            "price": float(ticker.get("lastPrice", 0) or 0) or None,
            "change_24h": round(float(ticker.get("priceChangePercent", 0) or 0), 2),
            "quote_volume": volume,
            "volume_tier": tier["key"],
            "volume_label": tier["label"],
            "analysis_available": contract.get("status") == "TRADING",
        }

    new_cutoff = now - max(1, int(new_days)) * DAY_MS
    delist_horizon = now + max(1, int(delist_days)) * DAY_MS
    upcoming_horizon = now + 90 * DAY_MS
    newly_listed, upcoming, delisting = [], [], []
    for contract in contracts:
        onboard = as_ms(contract.get("onboardDate"))
        delivery = as_ms(contract.get("deliveryDate"))
        status = str(contract.get("status") or "")
        if status == "TRADING" and new_cutoff <= onboard <= now + 5 * 60_000:
            newly_listed.append(item(contract, onboard, "new"))
        if (status in {"PENDING_TRADING", "PRE_TRADING"}
                and now - 14 * DAY_MS <= onboard <= upcoming_horizon):
            upcoming.append(item(contract, onboard, "upcoming"))
        if now < delivery <= delist_horizon and status in {
                "TRADING", "PRE_SETTLE", "SETTLING", "DELIVERING"}:
            delisting.append(item(contract, delivery, "delisting"))

    newly_listed.sort(key=lambda row: int(row["event_at"] or 0), reverse=True)
    upcoming.sort(key=lambda row: int(row["event_at"] or 0))
    delisting.sort(key=lambda row: int(row["event_at"] or 0))
    return {
        "new": newly_listed[:16],
        "upcoming": upcoming[:16],
        "delisting": delisting[:16],
        "source": "Binance Futures exchangeInfo",
        "checked_at": now,
    }


@router.get("/screener")
async def market_screener(
    limit: int = Query(180, ge=10, le=200),
    min_quote_volume: float = Query(5_000_000, ge=0),
    refresh: bool = False,
    _user=Depends(current_user),
):
    """Quant tarzı çok-zaman-dilimli piyasa tablosu.

    Bu uç nokta pozisyon, paper test veya kullanıcı riskini bilerek içermez.
    Piyasalar ekranının tek işi likit evreni aynı veri kesitinde kıyaslamaktır.
    """
    cache_key = f"market:screener:v2:{limit}:{int(min_quote_volume)}"

    async def build() -> Dict[str, Any]:
        tickers, symbols = await asyncio.gather(
            binance.ticker_24h(), binance.perpetual_symbols())
        perps = {r["symbol"]: r for r in symbols}
        liquid = [r for r in tickers if r.get("symbol") in perps
                  and float(r.get("quoteVolume", 0) or 0) >= min_quote_volume]
        liquid.sort(key=lambda r: float(r.get("quoteVolume", 0) or 0), reverse=True)
        targets = liquid[:limit]
        sem = asyncio.Semaphore(10)

        async def one(rank: int, ticker: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            symbol = ticker["symbol"]
            try:
                async with sem:
                    raw = await asyncio.gather(*(
                        binance.klines(symbol, interval, 120)
                        for interval in ("15m", "1h", "4h", "1d")
                    ))
                    premium, oi_rows = await asyncio.gather(
                        binance.premium_index(symbol),
                        binance.open_interest_hist(symbol, "1h", 25),
                    )
                frames = {interval: _screener_frame(rows)
                          for interval, rows in zip(("15m", "1h", "4h", "1d"), raw)}
                derivatives = _derivatives_frame(premium, oi_rows, raw[1])
                votes = sum(frame["votes"] for frame in frames.values())
                daily_close = binance.parse_klines(raw[-1])["close"]
                volume = float(ticker.get("quoteVolume", 0) or 0)
                tier = _volume_tier(volume)
                onboard = int(perps[symbol].get("onboardDate") or 0)
                listing_age_days = max(0, int((time.time() * 1000 - onboard) / DAY_MS)) if onboard else None
                return {
                    "rank": rank,
                    "symbol": symbol,
                    "price": float(ticker.get("lastPrice", 0) or 0),
                    "quote_volume": volume,
                    "volume_tier": tier["key"],
                    "volume_label": tier["label"],
                    "listing_age_days": listing_age_days,
                    "is_new": listing_age_days is not None and listing_age_days <= 45,
                    "change_24h": round(float(ticker.get("priceChangePercent", 0) or 0), 2),
                    "change_7d": _change(daily_close, 7),
                    "derivatives": derivatives,
                    "score": round(votes / 12.0 * 100.0),
                    "frames": frames,
                }
            except Exception:  # noqa: BLE001 - tek coin tüm tarayıcıyı düşürmemeli
                return None

        rows = [row for row in await asyncio.gather(*(
            one(rank, ticker) for rank, ticker in enumerate(targets, 1)
        )) if row]
        changes = [row["change_24h"] for row in rows]
        volume_bands = {"high": 0, "medium": 0, "emerging": 0}
        for ticker in liquid:
            volume_bands[_volume_tier(float(ticker.get("quoteVolume", 0) or 0))["key"]] += 1
        return {
            "rows": rows,
            "universe": len(liquid),
            "scanned": len(rows),
            "volume_bands": volume_bands,
            "breadth": {
                "up": sum(value > 0 for value in changes),
                "down": sum(value < 0 for value in changes),
                "average": round(sum(changes) / len(changes), 2) if changes else None,
            },
            "demo": binance.is_demo(),
        }

    return await build() if refresh else await cache.wrap(cache_key, 300.0, build)


@router.get("/listing-calendar")
async def listing_calendar(refresh: bool = False, _user=Depends(current_user)):
    """Yeni, yakinda acilacak ve gelecekte delist edilecek Futures kontratlari."""
    async def build() -> Dict[str, Any]:
        contracts, tickers = await asyncio.gather(
            binance.perpetual_contracts(), binance.ticker_24h())
        result = _listing_lifecycle(contracts, tickers)
        result["demo"] = binance.is_demo()
        return result

    key = "market:listing-calendar:v1"
    return await build() if refresh else await cache.wrap(key, 300.0, build)


@router.get("/prices")
async def prices(_user=Depends(current_user)):
    return {"ticks": binance_ws.snapshot(), "ws": binance_ws.status()}


@router.get("/rsi-radar")
async def rsi_radar(interval: str = "1h", limit: int = 40, context_limit: int = 24,
                    _user=Depends(current_user)):
    """Piyasa geneli RSI taramasi + ortalama RSI (ana panel kartinin kaynagi)."""
    data = await binance.ticker_24h()
    perps = {r["symbol"] for r in await binance.perpetual_symbols()}
    rows = [r for r in data if r["symbol"] in perps
            and float(r.get("quoteVolume", 0) or 0) >= 5_000_000]
    rows.sort(key=lambda r: float(r["quoteVolume"]), reverse=True)
    targets = [r["symbol"] for r in rows[:limit]]

    sem = asyncio.Semaphore(8)

    # Mumlar SEMBOL BASINA BIR KEZ cekiliyor ve hem RSI hem baglam ayni
    # seriden hesaplaniyor. Onceden radar 120, baglam 220 bar cekiyordu:
    # RSI ozyinelemeli (RMA) bir gosterge oldugu icin farkli isinma
    # penceresi FARKLI RSI uretiyordu ve ekranda BTCUSDT'nin RSI'i 72.22
    # yazarken siniflandirici onu asiri bolge saymiyordu. Tek cekim hem
    # bu tutarsizligi bitiriyor hem istek sayisini yariya indiriyor.
    bar_cache: Dict[str, Any] = {}

    async def one(sym: str) -> Optional[Dict[str, Any]]:
        async with sem:
            try:
                k = await binance.klines(sym, interval, 220)
                parsed = binance.parse_klines(k)
                from ..indicators.core import rsi as _rsi
                r = _rsi(parsed["close"], 14)
                val = float(r[-1])
                if val != val:
                    return None
                bar_cache[sym] = k
                return {"symbol": sym, "rsi": round(val, 2),
                        "price": float(parsed["close"][-1])}
            except Exception:  # noqa: BLE001
                return None

    results = [x for x in await asyncio.gather(*(one(s) for s in targets)) if x]

    # --- KATMAN 2: aşırı bölgedekiler için BAĞLAM ------------------------
    # Çıplak RSI'ın yön içeriği yok. Aşırı bölgeye girenler için rejim,
    # uyumsuzluk, kalıcılık, gerilme ve fonlamaya bakıp "devam mı tükenme
    # mi" sınıflandırması yapılıyor. Yalnızca aşırı bölgedekilere
    # bakılıyor: nötr bölgede sorulacak bir soru yok, boşuna istek olurdu.
    # Bağlam çıkarılacak semboller: iki uçtaki listelerde GÖRÜNEN her satır.
    # Eşiği geçmeyenlere aşırı bölge kararı verilemez ama trend okuması
    # verilebilir — panelde her satırda bir şey yazması için gereken bu.
    # Sonuçlar bu noktaya kadar hacim sırasındaydı. Doğrudan ilk/son 12'yi
    # almak "RSI uçları" yerine en yüksek ve en düşük hacimli coinleri
    # bağlam analizine sokuyordu. Önce RSI'a göre sırala; sayfadaki iki liste
    # ve motor beslemesi gerçekten aynı yükselen/düşen adayları okusun.
    ranked = sorted(results, key=lambda x: x["rsi"])
    ucta = ranked[:12] + ranked[-12:]
    hedef_sym = []
    for x in ucta:
        if x["symbol"] not in hedef_sym:
            hedef_sym.append(x["symbol"])
    asiri = [x for x in results if x["symbol"] in hedef_sym]
    asiri.sort(key=lambda x: -abs(x["rsi"] - 50))
    asiri = asiri[:context_limit]
    baglam: Dict[str, Dict[str, Any]] = {}

    async def ctx(sym: str) -> None:
        async with sem:
            try:
                k = bar_cache.get(sym)
                if not k:
                    return
                fon = None
                try:
                    pi = await binance.premium_index(sym)
                    if pi and pi.get("lastFundingRate") is not None:
                        fon = float(pi["lastFundingRate"]) * 10_000   # oran -> baz puan
                except Exception:  # noqa: BLE001
                    fon = None
                c = await asyncio.to_thread(rsi_context.classify, sym, k, fon)
                if c:
                    baglam[sym] = c
            except Exception:  # noqa: BLE001
                return

    if asiri:
        await asyncio.gather(*(ctx(x["symbol"]) for x in asiri))
        for x in results:
            if x["symbol"] in baglam:
                x["context"] = baglam[x["symbol"]]
        # Kararlari OLCULMEK uzere kaydet; N bar sonra fiyata bakilip
        # isabet edip etmedigi isaretlenecek.
        for c in baglam.values():
            try:
                rsi_context.record(c, interval)
            except Exception:  # noqa: BLE001
                pass

    values = [x["rsi"] for x in results]
    avg = round(sum(values) / len(values), 2) if values else None
    median = round(statistics.median(values), 2) if values else None
    results.sort(key=lambda x: x["rsi"])
    oversold = [x for x in results if x["rsi"] <= 30]
    overbought = [x for x in results if x["rsi"] >= 70]
    return {
        "interval": interval,
        "average_rsi": avg,
        "median_rsi": median,
        "count": len(results),
        "oversold": oversold[:12],
        "overbought": overbought[-12:][::-1],
        "lowest": [{**x, "ctx": baglam.get(x["symbol"])} for x in results[:12]],
        "highest": [{**x, "ctx": baglam.get(x["symbol"])} for x in results[-12:][::-1]],
        "distribution": {
            "oversold": len(oversold),
            "weak": sum(1 for v in values if 30 < v < 45),
            "neutral": sum(1 for v in values if 45 <= v <= 55),
            "strong": sum(1 for v in values if 55 < v < 70),
            "overbought": len(overbought),
        },
        "all": results,
        # Notr bolgeye dusenleri disari alma: baglam sorusu yalnizca
        # asiri bolge icin anlamli.
        "context": [c for c in baglam.values() if c.get("zone") != "neutral"],
        "context_scorecard": rsi_context.scorecard(30),
        "demo": binance.is_demo(),
    }


@router.get("/global")
async def global_data(_user=Depends(current_user)):
    fng, glob, gold = await asyncio.gather(
        market_extra.fear_greed(), market_extra.global_market(), market_extra.gold_spot())
    return {"fear_greed": fng, "global": glob, "gold": gold}
