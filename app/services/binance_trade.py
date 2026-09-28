"""Binance USDⓈ-M imzali istemci — hesap okuma ve emir katmani.

Emir fonksiyonlari burada yalnizca Binance protokolunu uygular. Risk,
idempotency, mevcut kullanici pozisyonuna dokunmama ve "stop kurulamazsa
hemen kapat" kurallari ``live_trade.py`` katmanindadir.

ANAHTAR SAKLAMA
---------------
Anahtarlar veritabaninda DUZ METIN durmuyor. `data/.trade_key` dosyasinda
(0600) bir Fernet anahtari uretiliyor ve API anahtarlari onunla sifrelenip
app_settings'e yaziliyor. Veritabani kopyalansa bile anahtar dosyasi
olmadan cozulemez.

Fernet anahtari settings.secret_key'e BAGLANMADI: o deger .env'de
tanimli degilse her aciliste yeniden uretiliyor ve sifreli anahtarlar
kalici olarak okunamaz hale gelirdi.

GUVENLIK KURALLARI (kod bunlari dogruluyor, ogut vermiyor)
  - API anahtarinda PARA CEKME yetkisi olmamali. Varsa baglanti
    "guvensiz" olarak isaretlenir.
  - IP kisitlamasi olmali. Yoksa uyarilir.
  - Emir yetkisi yoksa canli mod acilamaz.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

from .. import db
from ..config import DATA_DIR, settings

log = logging.getLogger("vortex.trade")

FAPI = "https://fapi.binance.com"
SAPI = "https://api.binance.com"
KEY_FILE = DATA_DIR / ".trade_key"
SETTING = "binance_trade_keys"
RECV_WINDOW = 5000


class BinanceTradeError(RuntimeError):
    """Binance'in kodunu ve mesajini kaybetmeden tasinan islem hatasi."""

    def __init__(self, status: int, payload: str):
        self.status = status
        self.payload = payload[:500]
        super().__init__(f"HTTP {status}: {self.payload}")


# --------------------------------------------------------------------------- #
# Anahtar saklama
# --------------------------------------------------------------------------- #
def _fernet():
    from cryptography.fernet import Fernet
    if KEY_FILE.exists():
        raw = KEY_FILE.read_bytes().strip()
    else:
        raw = Fernet.generate_key()
        KEY_FILE.write_bytes(raw)
        try:
            os.chmod(KEY_FILE, 0o600)     # sadece sahibi okusun
        except OSError:
            log.warning("%s izinleri 0600 yapilamadi", KEY_FILE)
        log.info("Islem anahtari dosyasi uretildi: %s", KEY_FILE)
    return Fernet(raw)


def save_keys(api_key: str, api_secret: str) -> None:
    f = _fernet()
    db.set_setting(SETTING, {
        "key": f.encrypt(api_key.strip().encode()).decode(),
        "secret": f.encrypt(api_secret.strip().encode()).decode(),
        "saved_at": db.now_ms(),
        # Kullanicinin dogru anahtari girdigini gorebilmesi icin son 4 hane.
        # Tamami hicbir zaman geri gosterilmiyor.
        "hint": api_key.strip()[-4:],
    })


def clear_keys() -> None:
    db.set_setting(SETTING, None)


def has_keys() -> bool:
    d = db.get_setting(SETTING) or {}
    return bool(d.get("key") and d.get("secret"))


def key_hint() -> Optional[str]:
    d = db.get_setting(SETTING) or {}
    return d.get("hint")


def _keys() -> Optional[tuple]:
    d = db.get_setting(SETTING) or {}
    if not (d.get("key") and d.get("secret")):
        return None
    try:
        f = _fernet()
        return f.decrypt(d["key"].encode()).decode(), f.decrypt(d["secret"].encode()).decode()
    except Exception as exc:  # noqa: BLE001
        log.error("Anahtarlar cozulemedi (anahtar dosyasi degismis olabilir): %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Imzali istek
# --------------------------------------------------------------------------- #
async def _signed(client: httpx.AsyncClient, base: str, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  method: str = "GET") -> Any:
    creds = _keys()
    if not creds:
        raise RuntimeError("API anahtari kayitli degil")
    api_key, api_secret = creds
    q = dict(params or {})
    q["timestamp"] = int(time.time() * 1000)
    q["recvWindow"] = RECV_WINDOW
    query = urlencode(q)
    sig = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{base}{path}?{query}&signature={sig}"
    resp = await client.request(method, url, headers={"X-MBX-APIKEY": api_key})
    if resp.status_code >= 400:
        # Binance hata kodlari anlamli; ham metni sakla, cevirisi cagirana ait
        raise BinanceTradeError(resp.status_code, resp.text)
    return resp.json()


async def signed(path: str, params: Optional[Dict[str, Any]] = None,
                 method: str = "GET") -> Any:
    """Uygulamanin tek imzali Futures istek kapisi."""
    async with httpx.AsyncClient(timeout=20.0,
                                 headers={"User-Agent": settings.USER_AGENT}) as client:
        return await _signed(client, FAPI, path, params, method)


async def public(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    async with httpx.AsyncClient(timeout=20.0,
                                 headers={"User-Agent": settings.USER_AGENT}) as client:
        resp = await client.get(f"{FAPI}{path}", params=params or {})
        if resp.status_code >= 400:
            raise BinanceTradeError(resp.status_code, resp.text)
        return resp.json()


def _step_floor(value: float, step: str) -> float:
    s = Decimal(str(step))
    if s <= 0:
        return float(value)
    v = Decimal(str(value))
    return float((v / s).to_integral_value(rounding=ROUND_DOWN) * s)


def _tick(value: float, tick: str, upward: bool = False) -> float:
    s = Decimal(str(tick))
    if s <= 0:
        return float(value)
    mode = ROUND_UP if upward else ROUND_DOWN
    v = Decimal(str(value))
    return float((v / s).to_integral_value(rounding=mode) * s)


def _plain_number(value: float) -> str:
    """Binance'a bilimsel gösterimsiz ve float artığı olmadan sayı gönder.

    `format(0.071, ".16g")` Python'da `0.07099999999999999` üretebiliyor.
    Binance bunu stepSize=0.001 olan kontratta fazla hassasiyet diye reddeder.
    Yuvarlama zaten exchangeInfo adımına göre yapıldığı için burada değeri
    Decimal(str(...)) üzerinden düz metne çevirmek yeterli ve kayıpsızdır.
    """
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("emir sayısı sonlu olmalı")
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


async def symbol_rules(symbol: str) -> Dict[str, Any]:
    """Miktar/fiyat yuvarlamasi icin borsanin guncel filtreleri."""
    info = await public("/fapi/v1/exchangeInfo")
    row = next((x for x in info.get("symbols", []) if x.get("symbol") == symbol), None)
    if not row:
        raise RuntimeError(f"{symbol} USD-M kontrati bulunamadi")
    filters = {x.get("filterType"): x for x in row.get("filters", [])}
    lot = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE") or {}
    # Bazi kontratlarda MARKET_LOT_SIZE stepSize=0 gelebilir; o durumda LOT_SIZE.
    if Decimal(str(lot.get("stepSize", "0"))) <= 0:
        lot = filters.get("LOT_SIZE") or lot
    notional = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
    return {
        "symbol": symbol,
        "status": row.get("status"),
        "tick_size": str((filters.get("PRICE_FILTER") or {}).get("tickSize", "0")),
        "step_size": str(lot.get("stepSize", "0")),
        "min_qty": float(lot.get("minQty", 0) or 0),
        "max_qty": float(lot.get("maxQty", 0) or 0),
        "min_notional": float(notional.get("notional", notional.get("minNotional", 5)) or 5),
    }


def floor_qty(value: float, rules: Dict[str, Any]) -> float:
    return _step_floor(value, rules["step_size"])


def trigger_price(value: float, rules: Dict[str, Any], upward: bool = False) -> float:
    return _tick(value, rules["tick_size"], upward)


async def position_mode() -> bool:
    """True = hedge, False = tek yon."""
    row = await signed("/fapi/v1/positionSide/dual")
    return bool(row.get("dualSidePosition"))


async def account_v3() -> Dict[str, Any]:
    return await signed("/fapi/v3/account")


async def account_v2() -> Dict[str, Any]:
    """canTrade hesabin v3 cevabinda yok; yetki bayragi v2'den okunur."""
    return await signed("/fapi/v2/account")


async def api_restrictions() -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0,
                                 headers={"User-Agent": settings.USER_AGENT}) as client:
        return await _signed(client, SAPI, "/sapi/v1/account/apiRestrictions")


async def position_rows(symbol: Optional[str] = None) -> list:
    params = {"symbol": symbol} if symbol else None
    return await signed("/fapi/v3/positionRisk", params)


async def open_algo_orders(symbol: Optional[str] = None) -> list:
    """Borsada halen acik duran kosullu stop/hedef emirleri.

    SARMALANMIS CEVAP SESSIZCE BOSA DUSMEMELI.
    Onceki surum `rows if isinstance(rows, list) else []` diyordu. Binance
    bu ucu bazi hesap tiplerinde {"orders": [...]} seklinde sarmalayarak
    donduruyor; o durumda fonksiyon "hic kosullu emir yok" cevabi
    veriyordu. Sonucu sessiz ve agirdi: her otomatik kayda initial_stop
    NULL yaziliyor, R hesabi tanim geregi o kayitlari disliyor ve KARNE
    BOS KALIYORDU — hicbir yerde sebebi yazmadan.

    Bilinen butun sarmalayicilar aciliyor; taninmayan bir sekil gelirse
    bos liste degil, HATA firlatiyoruz. Sessiz bos liste, gorulen en
    pahali hata bicimi oldu.
    """
    params = {"symbol": symbol} if symbol else None
    rows = await signed("/fapi/v1/openAlgoOrders", params)
    if isinstance(rows, list):
        return rows
    if isinstance(rows, dict):
        for anahtar in ("orders", "data", "rows", "result"):
            ic = rows.get(anahtar)
            if isinstance(ic, list):
                return ic
        # Bos sozluk = gercekten emir yok; bu mesru.
        if not rows:
            return []
    raise BinanceTradeError(
        f"openAlgoOrders beklenmeyen bicimde dondu: {type(rows).__name__} "
        f"{str(rows)[:120]}")


async def set_leverage(symbol: str, leverage: int) -> Dict[str, Any]:
    return await signed("/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}, "POST")


async def set_margin_type(symbol: str, margin_type: str = "ISOLATED") -> Dict[str, Any]:
    try:
        return await signed("/fapi/v1/marginType",
                            {"symbol": symbol, "marginType": margin_type}, "POST")
    except BinanceTradeError as exc:
        # -4046: "No need to change margin type."
        if "-4046" in exc.payload:
            return {"code": -4046, "msg": "already set"}
        raise


async def place_market(symbol: str, side: str, quantity: float,
                       position_side: str, client_id: str,
                       reduce_only: bool = False) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "symbol": symbol, "side": side, "type": "MARKET",
        "quantity": _plain_number(quantity), "positionSide": position_side,
        "newClientOrderId": client_id[:36], "newOrderRespType": "RESULT",
    }
    if reduce_only and position_side == "BOTH":
        params["reduceOnly"] = "true"
    return await signed("/fapi/v1/order", params, "POST")


async def query_order(symbol: str, client_id: str) -> Dict[str, Any]:
    return await signed("/fapi/v1/order", {
        "symbol": symbol, "origClientOrderId": client_id[:36],
    })


async def place_algo(symbol: str, side: str, order_type: str, quantity: float,
                     trigger: float, position_side: str, client_id: str) -> Dict[str, Any]:
    """Borsada duran stop/hedef. 09.12.2025 sonrasi Algo Service zorunlu."""
    params: Dict[str, Any] = {
        "algoType": "CONDITIONAL", "symbol": symbol, "side": side,
        "type": order_type, "quantity": _plain_number(quantity),
        "triggerPrice": _plain_number(trigger), "positionSide": position_side,
        "workingType": "MARK_PRICE", "priceProtect": "TRUE",
        "clientAlgoId": client_id[:36],
    }
    if position_side == "BOTH":
        params["reduceOnly"] = "true"
    return await signed("/fapi/v1/algoOrder", params, "POST")


async def cancel_algo(symbol: str, algo_id: Optional[int] = None,
                      client_algo_id: Optional[str] = None) -> Dict[str, Any]:
    params: Dict[str, Any] = {"symbol": symbol}
    if algo_id:
        params["algoId"] = algo_id
    elif client_algo_id:
        params["clientAlgoId"] = client_algo_id
    else:
        raise ValueError("algo_id veya client_algo_id gerekli")
    try:
        return await signed("/fapi/v1/algoOrder", params, "DELETE")
    except BinanceTradeError as exc:
        # Zaten tetiklenmis/iptal olmus koruma emri icin kapatma basarisiz sayilmaz.
        if any(code in exc.payload for code in ("-2011", "-2013", "-4139")):
            return {"already_closed": True}
        raise


async def test_order_permission() -> Dict[str, Any]:
    """Emir yetkisini gercek pozisyon acmadan borsa eslestirme katmaninda test et."""
    return await signed("/fapi/v1/order/test", {
        "symbol": "BTCUSDT", "side": "BUY", "type": "MARKET",
        "quantity": "0.001", "positionSide": "BOTH",
    }, "POST")


# --------------------------------------------------------------------------- #
# Baglanti testi — YALNIZCA OKUMA
# --------------------------------------------------------------------------- #
async def test_connection() -> Dict[str, Any]:
    """Anahtari ucdan uca dogrular ve GUVENLIK denetimi yapar.

    Hicbir emir gondermez, hicbir ayar degistirmez. Yalnizca okur.
    """
    out: Dict[str, Any] = {"ok": False, "checks": [], "hint": key_hint()}

    def add(name: str, ok: Optional[bool], detail: str, level: str = "info") -> None:
        out["checks"].append({"name": name, "ok": ok, "detail": detail, "level": level})

    if not has_keys():
        add("Anahtar", False, "Kayıtlı API anahtarı yok", "err")
        return out

    async with httpx.AsyncClient(timeout=20.0,
                                 headers={"User-Agent": settings.USER_AGENT}) as client:
        # 1) Futures hesabi okunabiliyor mu
        try:
            acc = await _signed(client, FAPI, "/fapi/v2/account")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "-2015" in msg:
                add("Bağlantı", False,
                    "Anahtar reddedildi (-2015). Sebebi genelde üçünden biri: anahtar yanlış, "
                    "IP kısıtlamasında bu sunucunun IP'si yok, ya da Futures yetkisi kapalı.", "err")
            elif "-1021" in msg:
                add("Bağlantı", False,
                    "Sunucu saati Binance ile uyumsuz (-1021). Sunucuda: "
                    "timedatectl set-ntp true", "err")
            else:
                add("Bağlantı", False, f"Futures hesabı okunamadı: {msg[:200]}", "err")
            return out

        add("Bağlantı", True, "Futures hesabı okunabiliyor", "ok")

        try:
            bal = float(acc.get("availableBalance", 0) or 0)
            wallet = float(acc.get("totalWalletBalance", 0) or 0)
            out["balance"] = {"available": round(bal, 2), "wallet": round(wallet, 2)}
            add("Bakiye", True, f"Cüzdan {wallet:.2f} USDT · kullanılabilir {bal:.2f} USDT", "ok")
        except (TypeError, ValueError):
            add("Bakiye", None, "Bakiye okunamadı", "warn")

        # 2) Emir yetkisi var mi
        can_trade = bool(acc.get("canTrade"))
        out["can_trade"] = can_trade
        add("Emir yetkisi", can_trade,
            "Açık — emir gönderilebilir" if can_trade
            else "KAPALI. Bu anahtarla emir gönderilemez.",
            "ok" if can_trade else "err")

        # 3) Acik pozisyon var mi (bilgi)
        try:
            pos = [p for p in acc.get("positions", [])
                   if abs(float(p.get("positionAmt", 0) or 0)) > 0]
            out["open_positions"] = [
                {"symbol": p["symbol"], "amt": float(p["positionAmt"]),
                 "entry": float(p.get("entryPrice", 0) or 0),
                 "pnl": round(float(p.get("unrealizedProfit", 0) or 0), 2)}
                for p in pos]
            add("Açık pozisyon", None,
                f"{len(pos)} açık pozisyon" if pos else "Açık pozisyon yok", "info")
        except Exception:  # noqa: BLE001
            pass

        # 4) GUVENLIK: para cekme yetkisi ve IP kisitlamasi
        try:
            r = await _signed(client, SAPI, "/sapi/v1/account/apiRestrictions")
            withdraw = bool(r.get("enableWithdrawals"))
            ip_locked = bool(r.get("ipRestrict"))
            out["withdraw_enabled"] = withdraw
            out["ip_restricted"] = ip_locked
            add("Para çekme yetkisi", not withdraw,
                "Kapalı — doğru" if not withdraw else
                "AÇIK. Bu anahtar hesabından para çekebilir. Binance'te bu yetkiyi KAPAT.",
                "ok" if not withdraw else "err")
            add("IP kısıtlaması", ip_locked,
                "Açık — anahtar sadece izinli IP'den kullanılabilir" if ip_locked else
                "KAPALI. Anahtar çalınırsa her yerden kullanılabilir. Binance'te sunucunun "
                "IP'sini ekleyip kısıtlamayı aç.",
                "ok" if ip_locked else "warn")
        except Exception as exc:  # noqa: BLE001
            add("Yetki denetimi", None,
                f"API kısıtlamaları okunamadı (bu uç nokta bazı anahtar tiplerinde kapalıdır): "
                f"{str(exc)[:120]}", "warn")

    # Genel sonuc: baglanti + emir yetkisi var VE para cekme KAPALI olmali
    hard = [c for c in out["checks"] if c["level"] == "err"]
    out["ok"] = not hard
    out["safe"] = out["ok"] and out.get("withdraw_enabled") is not True
    return out


async def account_summary() -> Dict[str, Any]:
    """Kisa hesap ozeti — panelde gostermek icin."""
    if not has_keys():
        return {"connected": False}
    async with httpx.AsyncClient(timeout=20.0,
                                 headers={"User-Agent": settings.USER_AGENT}) as client:
        try:
            acc = await _signed(client, FAPI, "/fapi/v2/account")
        except Exception as exc:  # noqa: BLE001
            return {"connected": False, "error": str(exc)[:200]}
    pos = [p for p in acc.get("positions", [])
           if abs(float(p.get("positionAmt", 0) or 0)) > 0]
    return {
        "connected": True,
        "wallet": round(float(acc.get("totalWalletBalance", 0) or 0), 2),
        "available": round(float(acc.get("availableBalance", 0) or 0), 2),
        "unrealized": round(float(acc.get("totalUnrealizedProfit", 0) or 0), 2),
        "can_trade": bool(acc.get("canTrade")),
        "open_positions": len(pos),
        "hint": key_hint(),
    }
