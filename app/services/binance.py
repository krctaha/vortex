"""Binance USDS-M Futures REST istemcisi.

- Public market-data endpoint'leri API anahtari GEREKTIRMEZ.
- Agirlik (weight) takibi: X-MBX-USED-WEIGHT-1M header'i okunur, %80'de yavaslatilir.
- Cografi kisit: bazi bolgelerden HTTP 451 doner. VORTEX_DATA_MODE=auto ise
  ilk 451/baglanti hatasinda DEMO moda duser ve durumu /api/system/status'ta bildirir.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings
from . import demo
from .cache import cache

log = logging.getLogger("vortex.binance")

WEIGHT_LIMIT = 2400
_state: Dict[str, Any] = {
    "mode": "demo" if settings.data_mode == "demo" else "unknown",
    "reason": "",
    "used_weight": 0,
    "last_ok": None,
    "last_error": None,
    "checked_at": 0.0,
    "recovered_at": None,     # demo'dan canliya en son ne zaman donuldu
    "recover_tries": 0,
}

_recover_task: Optional["asyncio.Task"] = None


def status() -> Dict[str, Any]:
    return dict(_state)


def is_demo() -> bool:
    return _state["mode"] == "demo"


class BinanceError(RuntimeError):
    pass


_client: Optional[httpx.AsyncClient] = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=settings.FAPI,
            timeout=httpx.Timeout(12.0, connect=6.0),
            headers={"User-Agent": settings.USER_AGENT,
                     **({"X-MBX-APIKEY": settings.binance_key} if settings.binance_key else {})},
            follow_redirects=True,
        )
    return _client


async def close() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _request(path: str, params: Optional[dict] = None, weight: int = 1) -> Any:
    if _state["used_weight"] > WEIGHT_LIMIT * 0.8:
        await asyncio.sleep(1.0)
    try:
        resp = await _http().get(path, params=params or {})
    except httpx.HTTPError as exc:
        # ProxyError, ConnectError, ReadTimeout, RemoteProtocolError ... hepsi buraya duser
        _mark_down(f"baglanti hatasi: {type(exc).__name__}: {exc}")
        raise BinanceError(f"{type(exc).__name__}: {exc}") from exc
    except OSError as exc:
        _mark_down(f"ag hatasi: {exc}")
        raise BinanceError(str(exc)) from exc

    used = resp.headers.get("X-MBX-USED-WEIGHT-1M")
    if used:
        try:
            _state["used_weight"] = int(used)
        except ValueError:
            pass

    if resp.status_code == 451:
        _mark_down("HTTP 451 — bu sunucunun bulundugu bolgeden Binance API'sine erisim engelli")
        raise BinanceError("HTTP 451 restricted location")
    if resp.status_code == 429:
        retry = int(resp.headers.get("Retry-After", "5"))
        await asyncio.sleep(min(retry, 30))
        raise BinanceError("HTTP 429 rate limit")
    if resp.status_code >= 400:
        raise BinanceError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    _state["mode"] = "live"
    _state["reason"] = ""
    _state["last_ok"] = time.time()
    return resp.json()


def _mark_down(reason: str) -> None:
    _state["last_error"] = reason
    if settings.data_mode == "live":
        _state["mode"] = "live"
        _state["reason"] = reason
        return
    if _state["mode"] != "demo":
        log.warning("Binance erisilemiyor (%s) -> DEMO moda gecildi", reason)
    _state["mode"] = "demo"
    _state["reason"] = reason


async def probe() -> Dict[str, Any]:
    """Acilista bir kez calisir: canli mi demo mu karar verir."""
    _state["checked_at"] = time.time()
    if settings.data_mode == "demo":
        _state["mode"] = "demo"
        _state["reason"] = "VORTEX_DATA_MODE=demo"
        return status()
    try:
        await _request("/fapi/v1/ping")
        _state["mode"] = "live"
        _state["reason"] = ""
        log.info("Binance CANLI mod aktif")
    except BinanceError as exc:
        _mark_down(str(exc))
    return status()


# --------------------------------------------------------------------------- #
# DEMO'DAN KURTARMA
#
# 29.08 DUZELTMESI — TEK YONLU KAPI HATASI
# Onceden: gecici bir ag hatasi _mark_down() ile modu "demo" yapiyordu. Ondan
# sonra klines/ticker/... hepsi ilk satirda is_demo() gorup kisa devre yapiyor,
# yani BIR DAHA ASLA gercek istek atilmiyordu. Modu "live"a geri cevirebilecek
# tek yer _request()'in sonu — oraya ulasmak icin istek atmak gerekiyordu.
# Sonuc: 10 saniyelik bir kesinti, sunucu sapasaglamken terminali yeniden
# baslatana kadar sentetik veride kilitliyordu.
#
# Cozum: demo moddayken duzenli araliklarla ping deneyen bagimsiz bir gorev.
# Basarili olursa mod kendiliginden canliya doner.
#
# NOT: VORTEX_DATA_MODE=demo ise bu dongu hic calismaz — o bilincli bir tercih,
# hata degil.
# --------------------------------------------------------------------------- #
RECOVER_INTERVAL = 60.0


async def _recover_loop() -> None:
    while True:
        try:
            await asyncio.sleep(RECOVER_INTERVAL)
            if settings.data_mode == "demo" or not is_demo():
                continue
            _state["recover_tries"] += 1
            # _request kendi basarisinda _state["mode"]="live" yapar
            await _request("/fapi/v1/ping")
            _state["recovered_at"] = time.time()
            log.info("Binance tekrar erisilebilir -> CANLI moda donuldu (%d deneme sonra)",
                     _state["recover_tries"])
            _state["recover_tries"] = 0
        except asyncio.CancelledError:
            raise
        except BinanceError:
            pass          # hala erisilemiyor; bir sonraki turda yine denenecek
        except Exception as exc:  # noqa: BLE001
            log.warning("Kurtarma dongusu hatasi: %s", exc)


async def start_recovery() -> None:
    global _recover_task
    if _recover_task is None or _recover_task.done():
        _recover_task = asyncio.create_task(_recover_loop(), name="vortex-binance-recover")


async def stop_recovery() -> None:
    global _recover_task
    if _recover_task and not _recover_task.done():
        _recover_task.cancel()
        try:
            await _recover_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _recover_task = None


# --------------------------------------------------------------------------- #
# Market data
# --------------------------------------------------------------------------- #
async def klines(symbol: str, interval: str = "15m", limit: int = 500) -> List[list]:
    limit = max(1, min(limit, 1500))
    if is_demo():
        return demo.klines(symbol, interval, limit)
    key = f"kl:{symbol}:{interval}:{limit}"
    ttl = 3.0 if interval in ("1m", "3m", "5m") else 12.0

    async def factory():
        try:
            w = 1 if limit <= 100 else 2 if limit <= 500 else 5 if limit <= 1000 else 10
            return await _request("/fapi/v1/klines",
                                  {"symbol": symbol.upper(), "interval": interval, "limit": limit}, w)
        except BinanceError:
            if settings.data_mode == "live":
                raise
            stale = cache.stale(key)
            return stale if stale is not None else demo.klines(symbol, interval, limit)

    return await cache.wrap(key, ttl, factory)


async def klines_range(symbol: str, interval: str, start_ms: int, end_ms: int,
                       limit: int = 1500) -> List[list]:
    """startTime/endTime ile tarihsel mum. Onbelleklenmez — backtest kendi
    onbellegini diskte tutar, RAM'de 3 aylik 5m veri tasimanin anlami yok."""
    limit = max(1, min(limit, 1500))
    if is_demo():
        return demo.klines_range(symbol, interval, start_ms, end_ms, limit)
    weight = 1 if limit <= 100 else 2 if limit <= 500 else 5 if limit <= 1000 else 10
    for attempt in range(4):
        try:
            return await _request("/fapi/v1/klines", {
                "symbol": symbol.upper(), "interval": interval,
                "startTime": int(start_ms), "endTime": int(end_ms), "limit": limit,
            }, weight)
        except BinanceError as exc:
            if attempt == 3:
                raise
            await asyncio.sleep(1.5 * (attempt + 1))
    return []


async def ticker_24h(symbol: Optional[str] = None) -> Any:
    if is_demo():
        return demo.ticker_24h([symbol] if symbol else None)[0] if symbol else demo.ticker_24h()
    key = f"t24:{symbol or 'ALL'}"

    async def factory():
        try:
            params = {"symbol": symbol.upper()} if symbol else None
            return await _request("/fapi/v1/ticker/24hr", params, 1 if symbol else 40)
        except BinanceError:
            if settings.data_mode == "live":
                raise
            stale = cache.stale(key)
            if stale is not None:
                return stale
            return demo.ticker_24h([symbol])[0] if symbol else demo.ticker_24h()

    return await cache.wrap(key, 6.0 if symbol else 20.0, factory)


async def premium_index(symbol: str) -> dict:
    if is_demo():
        return demo.premium_index(symbol)
    key = f"pi:{symbol}"

    async def factory():
        try:
            return await _request("/fapi/v1/premiumIndex", {"symbol": symbol.upper()})
        except BinanceError:
            if settings.data_mode == "live":
                raise
            return cache.stale(key) or demo.premium_index(symbol)

    return await cache.wrap(key, 8.0, factory)


async def open_interest_hist(symbol: str, period: str = "1h", limit: int = 48) -> List[dict]:
    if is_demo():
        return demo.open_interest_hist(symbol, period, limit)
    key = f"oi:{symbol}:{period}:{limit}"

    async def factory():
        try:
            return await _request("/futures/data/openInterestHist",
                                  {"symbol": symbol.upper(), "period": period, "limit": limit})
        except BinanceError:
            return cache.stale(key) or demo.open_interest_hist(symbol, period, limit)

    return await cache.wrap(key, 60.0, factory)


async def taker_long_short(symbol: str, period: str = "1h", limit: int = 24) -> List[dict]:
    """DIKKAT: endpoint yolu 'takerlongshortRatio' — kucuk harfli l ve s."""
    if is_demo():
        return []
    key = f"tls:{symbol}:{period}"

    async def factory():
        try:
            return await _request("/futures/data/takerlongshortRatio",
                                  {"symbol": symbol.upper(), "period": period, "limit": limit})
        except BinanceError:
            return cache.stale(key) or []

    return await cache.wrap(key, 60.0, factory)


async def funding_history(symbol: str, limit: int = 200) -> List[float]:
    """Gecmis fonlama oranlari (8 saatlik). limit=200 -> ~66 gun.

    NEDEN GECMIS DE GEREKIYOR: mutlak esik tek basina yaniltir. Bazi
    coinlerde %0.05 normaldir, bazisinda asiridir. Guncel orani KENDI
    gecmisinin icinde konumlandirmadan "kalabalik taraf" denemez.

    Fonlama gunde 3 kez guncellendigi icin onbellek 30 dakika: daha sik
    cekmenin bilgi degeri yok, sadece agirlik harciyor.
    """
    if is_demo():
        return []
    key = f"fh:{symbol}:{limit}"

    async def factory():
        try:
            rows = await _request("/fapi/v1/fundingRate",
                                  {"symbol": symbol.upper(), "limit": min(limit, 1000)})
        except BinanceError:
            return cache.stale(key) or []
        out = []
        for r in rows or []:
            try:
                out.append(float(r["fundingRate"]))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    return await cache.wrap(key, 1800.0, factory)


async def global_long_short(symbol: str, period: str = "1h", limit: int = 24) -> List[dict]:
    if is_demo():
        return []
    key = f"gls:{symbol}:{period}"

    async def factory():
        try:
            return await _request("/futures/data/globalLongShortAccountRatio",
                                  {"symbol": symbol.upper(), "period": period, "limit": limit})
        except BinanceError:
            return cache.stale(key) or []

    return await cache.wrap(key, 60.0, factory)


async def funding_range(symbol: str, start_ms: int, end_ms: int) -> List[dict]:
    """Tarihsel fonlama oranlari, zaman damgasiyla. Backtest icin.

    funding_history() son N kaydi dondurur; backtest ise "o an gecerli olan
    oran" ister. Onbelleklenmez — backtest kendi diskinde tutuyor.
    """
    if is_demo():
        return []
    out: List[dict] = []
    cursor = int(start_ms)
    for _ in range(40):                       # 40 x 1000 = 333 gunden fazlasi
        try:
            rows = await _request("/fapi/v1/fundingRate",
                                  {"symbol": symbol.upper(), "startTime": cursor,
                                   "endTime": int(end_ms), "limit": 1000})
        except BinanceError:
            break
        if not rows:
            break
        out.extend(rows)
        last = int(rows[-1].get("fundingTime", cursor))
        if last <= cursor:
            break
        cursor = last + 1
        if len(rows) < 1000:
            break
        await asyncio.sleep(0.12)
    return out


def _perpetual_contract_row(s: Dict[str, Any]) -> Dict[str, Any]:
    filters = {f["filterType"]: f for f in s.get("filters", [])}
    return {
        "symbol": s["symbol"],
        "baseAsset": s.get("baseAsset") or s["symbol"].removesuffix("USDT"),
        "quoteAsset": s.get("quoteAsset", "USDT"),
        "contractType": s.get("contractType", "PERPETUAL"),
        "status": s.get("status", "TRADING"),
        # Binance Futures exchangeInfo bu iki tarihi milisaniye olarak verir.
        # Motor onboardDate'i yas filtresinde, Piyasalar ise yeni liste/delist
        # takviminde kullanir. deliveryDate=2100 civari aktif perpetual icin
        # "planli teslim yok" sentinelidir; UI bunu delist diye gostermemeli.
        "onboardDate": s.get("onboardDate"),
        "deliveryDate": s.get("deliveryDate"),
        "pricePrecision": s.get("pricePrecision"),
        "quantityPrecision": s.get("quantityPrecision"),
        "tickSize": filters.get("PRICE_FILTER", {}).get("tickSize"),
        "stepSize": filters.get("LOT_SIZE", {}).get("stepSize"),
        "minNotional": filters.get("MIN_NOTIONAL", {}).get("notional"),
    }


async def perpetual_contracts() -> List[dict]:
    """TRADING ve gecis durumlari dahil tum USDT perpetual sozlesmeleri.

    Emir motoru bu listenin tamamini kullanmaz. Yeni liste ve delist takvimi
    icin PENDING_TRADING/PRE_SETTLE durumlarini gorebilmek amaciyla ayri tutulur.
    """
    if is_demo():
        return [_perpetual_contract_row(s) for s in demo.exchange_symbols()]

    async def factory():
        try:
            info = await _request("/fapi/v1/exchangeInfo")
        except BinanceError:
            if settings.data_mode == "live":
                raise
            return cache.stale("perpetual_contracts") or [
                _perpetual_contract_row(s) for s in demo.exchange_symbols()]
        return [_perpetual_contract_row(s) for s in info.get("symbols", [])
                if s.get("quoteAsset") == "USDT"
                and s.get("contractType") == "PERPETUAL"]

    return await cache.wrap("perpetual_contracts", 300.0, factory)


async def perpetual_symbols() -> List[dict]:
    """Sadece emir/tarama icin uygun TRADING USDT PERPETUAL kontratlar."""
    return [s for s in await perpetual_contracts() if s.get("status") == "TRADING"]


def parse_klines(rows: List[list]) -> Dict[str, Any]:
    """Binance kline dizisini numpy dizilerine cevirir."""
    import numpy as np
    if not rows:
        empty = np.array([], dtype=float)
        return {"open_time": empty, "open": empty, "high": empty, "low": empty,
                "close": empty, "volume": empty, "taker_buy": empty, "close_time": empty}
    arr = list(zip(*rows))
    f = lambda idx: np.array([float(x) for x in arr[idx]], dtype=float)  # noqa: E731
    return {
        "open_time": np.array([int(x) for x in arr[0]], dtype=np.int64),
        "open": f(1), "high": f(2), "low": f(3), "close": f(4), "volume": f(5),
        "close_time": np.array([int(x) for x in arr[6]], dtype=np.int64),
        "quote_volume": f(7),
        "taker_buy": f(9),
    }
