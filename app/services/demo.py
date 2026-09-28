"""DEMO veri uretici.

Binance'e ulasilamayan ortamlarda (or. ABD IP -> HTTP 451) arayuzun ve
indikatorlerin calistigini dogrulamak icin gercekci sentetik veri uretir.
Uretilen her sey UI'da 'DEMO' rozetiyle isaretlenir - gercek fiyat DEGILDIR.
Ayni sembol + interval icin deterministiktir (tohum sembol adindan turetilir).
"""
from __future__ import annotations

import hashlib
import math
import time
from typing import List

import numpy as np

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
}

BASE_PRICE = {
    "BTCUSDT": 79000.0, "ETHUSDT": 3050.0, "XAUUSDT": 4640.0, "SOLUSDT": 178.0,
    "BNBUSDT": 640.0, "XRPUSDT": 2.35, "DOGEUSDT": 0.21, "ADAUSDT": 0.72,
    "AVAXUSDT": 32.0, "LINKUSDT": 21.5, "TONUSDT": 4.8, "SUIUSDT": 3.4,
}

DEMO_SYMBOLS = list(BASE_PRICE.keys()) + [
    "OPUSDT", "ARBUSDT", "APTUSDT", "INJUSDT", "NEARUSDT", "FILUSDT",
    "LTCUSDT", "ATOMUSDT", "DOTUSDT", "TRXUSDT", "PEPEUSDT", "WIFUSDT",
]


def _seed(symbol: str, interval: str) -> int:
    return int(hashlib.md5(f"{symbol}:{interval}".encode()).hexdigest()[:8], 16)


def base_price(symbol: str) -> float:
    if symbol in BASE_PRICE:
        return BASE_PRICE[symbol]
    h = int(hashlib.md5(symbol.encode()).hexdigest()[:6], 16)
    return round(0.5 + (h % 20000) / 100.0, 4)


def klines(symbol: str, interval: str = "15m", limit: int = 500) -> List[list]:
    step = INTERVAL_MS.get(interval, 900_000)
    rng = np.random.default_rng(_seed(symbol, interval))
    n = limit
    price0 = base_price(symbol)
    vol_daily = 0.035 if symbol not in ("XAUUSDT",) else 0.012
    sigma = vol_daily * math.sqrt(step / 86_400_000)
    # trend + ortalamaya donus karisimi, gercekci otokorelasyon
    shocks = rng.standard_normal(n) * sigma
    drift = np.sin(np.linspace(0, rng.uniform(2, 7), n)) * sigma * 0.45
    logret = shocks + drift
    path = np.cumsum(logret)
    # Seriyi, SON kapanis base_price'a yakin kalacak sekilde yeniden merkezle;
    # aksi halde rastgele yuruyus gunler icinde gercekci olmayan seviyelere kayar.
    # Ofset SADECE sembole bagli (interval'e degil): boylece 15m, 1h ve 4h
    # serileri AYNI son fiyatta biter ve panelde celiskili fiyat gorunmez.
    level = np.random.default_rng(_seed(symbol, "level")).normal(0.0, 0.012)
    path = path - path[-1] + level
    close = price0 * np.exp(path)
    open_ = np.concatenate([[price0], close[:-1]])
    wick = np.abs(rng.standard_normal(n)) * sigma * 0.9
    high = np.maximum(open_, close) * (1 + wick)
    low = np.minimum(open_, close) * (1 - wick)
    notional = max(price0 * 40, 1000)
    volume = np.abs(rng.lognormal(0, 0.7, n)) * (notional / max(price0, 1e-9))
    taker_buy = volume * rng.uniform(0.35, 0.65, n)

    now = int(time.time() * 1000)
    start = now - (now % step) - step * (n - 1)
    out = []
    for i in range(n):
        ot = start + i * step
        out.append([
            ot,
            f"{open_[i]:.8f}", f"{high[i]:.8f}", f"{low[i]:.8f}", f"{close[i]:.8f}",
            f"{volume[i]:.8f}",
            ot + step - 1,
            f"{volume[i] * close[i]:.8f}",
            int(200 + abs(rng.standard_normal()) * 800),
            f"{taker_buy[i]:.8f}",
            f"{taker_buy[i] * close[i]:.8f}",
            "0",
        ])
    return out


def klines_range(symbol: str, interval: str, start_ms: int, end_ms: int,
                 limit: int = 1500) -> List[list]:
    """Belirtilen araligi kaplayan sentetik mumlar (yalnizca duman testi icin)."""
    step = INTERVAL_MS.get(interval, 900_000)
    total = max(int((end_ms - start_ms) // step), 1)
    n = min(total, limit)
    base = klines(symbol, interval, n)
    start = start_ms - (start_ms % step)
    out = []
    for i, row in enumerate(base):
        ot = start + i * step
        out.append([ot] + row[1:6] + [ot + step - 1] + row[7:])
    return out


def ticker_24h(symbols: List[str] | None = None) -> List[dict]:
    symbols = symbols or DEMO_SYMBOLS
    out = []
    for s in symbols:
        k = klines(s, "1h", 25)
        first = float(k[0][1])
        last = float(k[-1][4])
        highs = [float(x[2]) for x in k]
        lows = [float(x[3]) for x in k]
        qv = sum(float(x[7]) for x in k) * 60
        out.append({
            "symbol": s,
            "lastPrice": f"{last:.8f}",
            "openPrice": f"{first:.8f}",
            "highPrice": f"{max(highs):.8f}",
            "lowPrice": f"{min(lows):.8f}",
            "priceChange": f"{last - first:.8f}",
            "priceChangePercent": f"{(last - first) / first * 100:.3f}",
            "volume": f"{qv / max(last, 1e-9):.4f}",
            "quoteVolume": f"{qv:.4f}",
            "count": 100000,
        })
    return out


def premium_index(symbol: str) -> dict:
    rng = np.random.default_rng(_seed(symbol, "funding"))
    last = float(klines(symbol, "1h", 2)[-1][4])
    return {
        "symbol": symbol,
        "markPrice": f"{last:.8f}",
        "indexPrice": f"{last * 0.9998:.8f}",
        "lastFundingRate": f"{rng.normal(0.0001, 0.00025):.8f}",
        "nextFundingTime": (int(time.time() * 1000) // 28_800_000 + 1) * 28_800_000,
        "time": int(time.time() * 1000),
    }


def open_interest_hist(symbol: str, period: str = "1h", limit: int = 48) -> List[dict]:
    step = INTERVAL_MS.get(period, 3_600_000)
    rng = np.random.default_rng(_seed(symbol, "oi" + period))
    base = base_price(symbol)
    oi = np.abs(np.cumsum(rng.standard_normal(limit) * 0.02) + 10) * 1000
    now = int(time.time() * 1000)
    start = now - (now % step) - step * (limit - 1)
    return [{
        "symbol": symbol,
        "sumOpenInterest": f"{oi[i]:.4f}",
        "sumOpenInterestValue": f"{oi[i] * base:.4f}",
        "timestamp": str(start + i * step),
    } for i in range(limit)]


def exchange_symbols() -> List[dict]:
    return [{
        "symbol": s, "baseAsset": s.replace("USDT", ""), "quoteAsset": "USDT",
        "contractType": "PERPETUAL", "status": "TRADING",
        "onboardDate": int(time.time() * 1000) - 400 * 86_400_000,
        "pricePrecision": 4, "quantityPrecision": 3,
        "tickSize": "0.0001", "stepSize": "0.001", "minNotional": "5",
    } for s in DEMO_SYMBOLS]


# --------------------------------------------------------------------------- #
# Binance disi kaynaklarin demo karsiliklari
# --------------------------------------------------------------------------- #
def fear_greed() -> dict:
    rng = np.random.default_rng(int(time.time()) // 86400)
    v = int(np.clip(rng.normal(58, 16), 5, 95))
    hist = [{"t": int((time.time() - (7 - i) * 86400) * 1000),
             "v": int(np.clip(rng.normal(v, 8), 5, 95))} for i in range(8)]
    hist[-1]["v"] = v
    return {"value": v, "label": "Greed" if v > 55 else "Neutral",
            "label_tr": ("Aşırı Korku" if v <= 24 else "Korku" if v <= 44
                         else "Nötr" if v <= 55 else "Açgözlülük" if v <= 74
                         else "Aşırı Açgözlülük"),
            "previous": hist[-2]["v"], "history": hist,
            "updated": int(time.time() * 1000), "source": "DEMO", "ok": True, "demo": True}


def global_market() -> dict:
    rng = np.random.default_rng(int(time.time()) // 3600)
    btc_d = round(float(np.clip(rng.normal(57.5, 1.2), 45, 68)), 2)
    eth_d = round(float(np.clip(rng.normal(11.4, 0.6), 6, 20)), 2)
    total = float(rng.normal(2.78e12, 4e10))
    return {"btc_dominance": btc_d, "eth_dominance": eth_d,
            "total_market_cap": total,
            "total2": round(total * (1 - btc_d / 100), 2),
            "total3": round(total * (1 - (btc_d + eth_d) / 100), 2),
            "volume_24h": float(rng.normal(2.3e11, 2e10)),
            "market_cap_change_24h": round(float(rng.normal(0.2, 1.8)), 2),
            "source": "DEMO", "ok": True, "demo": True}


def gold_spot() -> dict:
    return {"price": round(base_price("XAUUSDT") * float(np.random.default_rng(
        int(time.time()) // 600).normal(1.0, 0.002)), 2),
        "updated": None, "source": "DEMO", "ok": True, "demo": True}


def economic_calendar() -> dict:
    now = time.time()
    seeds = [
        ("Core PCE Price Index m/m", "USD", "High", 6, "0.2%", "0.1%", True),
        ("Prelim GDP q/q", "USD", "High", 6, "1.5%", "1.5%", True),
        ("FOMC Member Speaks", "USD", "Medium", 20, None, None, False),
        ("Fed Chair Speaks", "USD", "High", 44, None, None, True),
        ("Unemployment Claims", "USD", "Medium", 30, "228K", "235K", False),
        ("CPI y/y", "EUR", "High", 52, "2.1%", "2.2%", True),
        ("Core Retail Sales m/m", "CAD", "Low", 68, "0.3%", "0.5%", False),
        ("Non-Farm Employment Change", "USD", "High", 90, "168K", "142K", True),
    ]
    events = [{
        "title": t, "currency": cur, "impact": imp,
        "impact_tr": {"High": "Yüksek", "Medium": "Orta", "Low": "Düşük"}[imp],
        "time": int((now + h * 3600) * 1000),
        "forecast": f, "previous": p, "critical": crit,
    } for t, cur, imp, h, f, p, crit in seeds]
    return {"ok": True, "events": events, "source": "DEMO",
            "fetched": int(now * 1000), "demo": True}


def news(limit: int = 30) -> dict:
    now = time.time()
    seeds = [
        ("Bitcoin 79.000 dolar bandında tutunuyor, altcoinlerde kar satışı", "Uzmancoin", "tr", 0.6),
        ("Fed tutanakları öncesi kripto piyasasında temkinli seyir", "Uzmancoin", "tr", 2.1),
        ("Bitcoin holds $79,000 as traders bank a week of gains", "CoinDesk", "en", 3.4),
        ("Spot ETF girişleri üst üste dördüncü haftada pozitif", "Coin Mühendisi", "tr", 5.0),
        ("Ethereum staking çıkış kuyruğu 9 günlük seviyeye geriledi", "Cointelegraph", "en", 7.2),
        ("Crypto greed gauge hits highest level since October", "CoinDesk", "en", 9.5),
        ("XRP davasında yeni gelişme: taraflar uzlaşma başvurusu yaptı", "Coin Mühendisi", "tr", 12.0),
        ("Solana ağında günlük aktif adres rekoru kırıldı", "Uzmancoin", "tr", 15.5),
        ("Altın 4.600 dolar üzerinde, jeopolitik risk primi sürüyor", "Uzmancoin", "tr", 18.0),
        ("Analistler: Bitcoin için 83.000 dolar kritik direnç", "Cointelegraph", "en", 21.0),
    ]
    items = [{
        "title": t, "link": "#", "guid": f"demo-{i}",
        "published": int((now - h * 3600) * 1000),
        "source": s, "lang": lang, "demo": True,
    } for i, (t, s, lang, h) in enumerate(seeds)]
    return {"ok": True, "items": items[:limit], "errors": [],
            "fetched": int(now * 1000), "demo": True}
