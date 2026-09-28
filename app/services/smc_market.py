"""Bounded public-data reader: no synthetic/stale fallback in live mode."""
import asyncio
import time
from . import binance
from .cache import cache
from ..config import settings

_gate = asyncio.Lock()
_next_request = 0.0

async def request(path, params=None, weight=1):
    global _next_request
    async with _gate:
        await asyncio.sleep(max(0, _next_request - time.monotonic()))
        _next_request = time.monotonic() + weight / 12.0
    return await binance._request(path, params, weight)

def demo_mode():
    return settings.data_mode == "demo"

async def universe(limit=200):
    if demo_mode():
        contracts, tickers = await asyncio.gather(binance.perpetual_symbols(), binance.ticker_24h())
    else:
        info, tickers = await asyncio.gather(
            cache.wrap("smc:contracts", 3600, lambda: request("/fapi/v1/exchangeInfo")),
            cache.wrap("smc:tickers", 10, lambda: request("/fapi/v1/ticker/24hr", weight=40)))
        contracts = info["symbols"]
    allowed = {r["symbol"]: r for r in contracts if r.get("status") == "TRADING"
               and r.get("quoteAsset") == "USDT" and r.get("contractType") == "PERPETUAL"
               and r.get("underlyingType", "COIN") == "COIN"}
    selected, seen = [], set()
    for row in sorted(tickers, key=lambda r: float(r.get("quoteVolume", 0)), reverse=True):
        symbol = row["symbol"]
        contract = allowed.get(symbol)
        if not contract:
            continue
        base = contract.get("baseAsset") or symbol
        if base in seen:
            continue
        seen.add(base)
        selected.append({"symbol": symbol, "price": float(row["lastPrice"]),
                         "change": float(row["priceChangePercent"]),
                         "volume": float(row["quoteVolume"])})
        if len(selected) >= limit:
            break
    if not selected:
        raise ValueError("No eligible perpetual markets")
    return selected

async def funding():
    if demo_mode():
        return {}
    rows = await cache.wrap("smc:funding", 30, lambda: request("/fapi/v1/premiumIndex", weight=10))
    return {r["symbol"]: float(r["lastFundingRate"]) for r in rows}

async def candles(symbol, interval, limit):
    if demo_mode():
        return await binance.klines(symbol, interval, limit)
    seconds = {"4h": 14400, "15m": 900}[interval]
    ttl = max(1, seconds - ((time.time() - 3) % seconds))
    weight = 2 if limit < 500 else 5
    return await cache.wrap(f"smc:kl:{symbol}:{interval}:{limit}", ttl,
                            lambda: request("/fapi/v1/klines", {
                                "symbol": symbol, "interval": interval, "limit": limit}, weight))

async def dashboard():
    rows, rates = await asyncio.gather(universe(), funding())
    return {"markets": [{**row, "funding": rates.get(row["symbol"])} for row in rows],
            "updated_at": int(time.time() * 1000), "demo": demo_mode()}
