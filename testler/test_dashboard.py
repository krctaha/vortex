import asyncio
import time
import pytest
from app.services import smc_market as market, smc_ict_engine as engine, binance, premium_engine
from app.services import binance_ws

def test_live_price_rejects_stale_tick(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr(binance_ws.time, "time", lambda: now)
    monkeypatch.setattr(binance_ws, "_ticks", {"BTCUSDT": {"price": 100.0, "ts": int(now*1000)-10001}})
    assert binance_ws.get_price("BTCUSDT") is None
    binance_ws._ticks["BTCUSDT"]["ts"] = int(now*1000)-1000
    assert binance_ws.get_price("BTCUSDT") == 100.0

@pytest.mark.asyncio
async def test_dashboard_retains_structural_summary_and_verification_score(monkeypatch):
    from app.api import engine as api
    row={"symbol":"BTCUSDT","ok":True,"demo":False,"side":"LONG","phase":"MSS_BEKLE",
         "score":65,"verification":{"score":70,"components":[{"large":"detail"}]},"ready":False}
    monkeypatch.setattr(api.premium_engine,"status",lambda:{"results":[row]})
    monkeypatch.setattr(api.smc_board,"snapshot",lambda scanner:{})
    async def dashboard():return {"markets":[]}
    monkeypatch.setattr(api.smc_market,"dashboard",dashboard)
    result=await api.smc_dashboard()
    summary=result["scanner"]["results"][0]
    assert summary["ok"] and summary["demo"] is False and summary["side"]=="LONG"
    assert summary["verification"]=={"score":70}
    assert row["verification"]["components"]  # Source observations are not mutated.

@pytest.mark.asyncio
async def test_universe_200_unique_trading_coins(monkeypatch):
    contracts = [{"symbol": f"C{i}USDT", "baseAsset": f"C{i}", "quoteAsset": "USDT",
                  "status": "TRADING", "contractType": "PERPETUAL", "underlyingType": "COIN"} for i in range(210)]
    contracts += [{**contracts[0], "symbol": "ALIASUSDT"}, {**contracts[0], "symbol": "STOCKUSDT", "underlyingType": "INDEX"}]
    tickers = [{"symbol": c["symbol"], "quoteVolume": str(10000-i), "lastPrice": "100", "priceChangePercent": "2"} for i,c in enumerate(contracts)]
    async def wrapped(key, ttl, factory):
        return {"symbols": contracts} if key == "smc:contracts" else tickers
    monkeypatch.setattr(market, "demo_mode", lambda: False)
    monkeypatch.setattr(market.cache, "wrap", wrapped)
    rows = await market.universe()
    assert len(rows) == len({r["symbol"] for r in rows}) == 200
    assert rows[0]["symbol"] == "C0USDT"
    assert not any(r["symbol"] in ("ALIASUSDT", "STOCKUSDT") for r in rows)

@pytest.mark.asyncio
async def test_same_candle_cached_concurrent_scans_and_next_candle_refresh(monkeypatch):
    now = [int(time.time()//900)*900+20]
    calls = []
    async def universe(limit):
        return [{"symbol": "BTCUSDT"}, {"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]
    async def funding():
        return {"BTCUSDT": 0.0, "ETHUSDT": 0.0}
    async def candles(symbol, interval, limit):
        calls.append((symbol,interval))
        return [[0,100,101,99,100,10,int(now[0]//900)*900_000-1]]
    def analyze(symbol,*args):
        return {"symbol":symbol,"ok":True,"score":80,"ready":True,"as_of":int(now[0]//900)*900_000-1}
    monkeypatch.setattr(engine, "_scan_cache", {})
    monkeypatch.setattr(engine, "_scan_lock", asyncio.Lock())
    monkeypatch.setattr(engine.time, "time", lambda: now[0])
    monkeypatch.setattr(engine, "analyze_rows", analyze)
    monkeypatch.setattr(market, "universe", universe)
    monkeypatch.setattr(market, "funding", funding)
    monkeypatch.setattr(market, "candles", candles)
    monkeypatch.setattr(market, "demo_mode", lambda: False)
    first, second = await asyncio.gather(engine.scan(),engine.scan())
    assert first["scanned"] == 2 and second["reused"] == 2
    assert len(calls) == 4
    now[0] += 900
    assert (await engine.scan())["reused"] == 0
    assert len(calls) == 8

@pytest.mark.asyncio
@pytest.mark.parametrize("reader,args", [("klines", ("BTCUSDT",)), ("ticker_24h", ()), ("premium_index", ("BTCUSDT",)), ("perpetual_contracts", ())])
async def test_live_outage_never_returns_synthetic_data(monkeypatch, reader, args):
    async def fail(*a, **kw):
        raise binance.BinanceError("offline")
    async def wrapped(key, ttl, factory):
        return await factory()
    monkeypatch.setattr(binance.settings, "data_mode", "live")
    monkeypatch.setattr(binance, "is_demo", lambda: False)
    monkeypatch.setattr(binance, "_request", fail)
    monkeypatch.setattr(binance.cache, "wrap", wrapped)
    with pytest.raises(binance.BinanceError):
        await getattr(binance, reader)(*args)

def test_stale_status_cannot_advertise_ready_plan(monkeypatch):
    monkeypatch.setattr(premium_engine, "_STATE", {"last_scan_at": 1, "candidates":[{"ready":True}]})
    assert premium_engine.status()["candidates"][0]["ready"] is False
