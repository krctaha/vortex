"""Canli fiyat yayini.

Mimari: tarayici -> VORTEX WS -> (tek) Binance combined stream.
Boylece her tarayici sekmesi Binance'e ayri baglanti acmaz, 200 stream/baglanti
ve 5 mesaj/sn abonelik limitleri tek yerden yonetilir.

Fiyat kaynagi olarak bookTicker (real-time, tamponsuz) kullanilir;
markPrice en hizli haliyle 1sn'dir ve zaten turetilmis/duzlestirilmis bir
fiyattir - sub-saniye gorunum icin yanlis seciмdir.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import re
import time
from typing import Any, Dict, Optional, Set

from ..config import settings
from . import binance, demo, smc_rsi

log = logging.getLogger("vortex.ws")

# symbol -> son tick
_ticks: Dict[str, Dict[str, Any]] = {}
_flows: Dict[str, Dict[str, float]] = {}
_subscribers: Set[asyncio.Queue] = set()
_symbols: Set[str] = set()
_task: Optional[asyncio.Task] = None
_flow_task: Optional[asyncio.Task] = None
_trade_task = None
_quotes = {}
_candles = {}
_trade_connections = set()
_market_stats = {}
_restart = asyncio.Event()
_state = {"connected": False, "mode": "idle", "last_message": None, "reconnects": 0}


def status() -> Dict[str, Any]:
    return {**_state, "symbols": sorted(_symbols), "subscribers": len(_subscribers)}


def snapshot() -> Dict[str, Dict[str, Any]]:
    return dict(_ticks)


def get_price(symbol: str) -> Optional[float]:
    t = _ticks.get(symbol.upper())
    return t.get("price") if t and time.time() * 1000 - t.get("ts", 0) < 10_000 else None


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def watch(symbols) -> None:
    """Izlenecek sembol kumesini genislet; degisiklik varsa upstream yeniden kurulur."""
    new = {s.upper() for s in symbols[:256] if isinstance(s, str) and re.fullmatch(r"[A-Za-z0-9]{3,24}", s)}
    new = set(sorted(new - _symbols)[:max(0,256-len(_symbols))])
    if new - _symbols:
        _symbols.update(new)
        _restart.set()


def set_watch(symbols) -> None:
    global _symbols
    new = {s.upper() for s in symbols if s}
    if new != _symbols:
        _symbols = new
        _restart.set()


def _publish(tick: Dict[str, Any]) -> None:
    if tick.get('source')=='aggTrade' and not tick.get('demo'):
        tick['rsi_14']=smc_rsi.value(tick['symbol'],tick['price'],tick['ts'])
    _ticks[tick["symbol"]] = tick
    _state["last_message"] = time.time()
    _emit(tick)


def _emit(tick):
    dead = []
    for q in _subscribers:
        try:
            q.put_nowait(tick)
        except asyncio.QueueFull:
            # Yavas istemci: en eskiyi at, yenisini koy (fiyatta tazelik > butunluk)
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(tick)
        except Exception:  # noqa: BLE001
            dead.append(q)
    for q in dead:
        _subscribers.discard(q)


def _flow_fields(symbol: str) -> Dict[str, Any]:
    f = _flows.get(symbol) or {}
    buy, sell = float(f.get("buy", 0)), float(f.get("sell", 0))
    total = buy + sell
    return {"flow_buy_usdt": round(buy, 2), "flow_sell_usdt": round(sell, 2),
            "flow_delta_usdt": round(buy - sell, 2),
            "flow_buy_ratio": round(buy / total * 100, 2) if total else 50.0,
            "flow_window_ms": 60000}


# --------------------------------------------------------------------------- #
# Demo tick uretici
# --------------------------------------------------------------------------- #
async def _demo_loop() -> None:
    _state.update(connected=True, mode="demo")
    log.info("WS DEMO modda calisiyor (sentetik tick)")
    base: Dict[str, float] = {}
    # 29.08 DUZELTMESI — korumasiz dongu. _live_loop baglanti kaybinda BURAYA
    # dusuyor; burasi da olurse tick akisi tamamen duruyordu.
    while True:
      try:
        if _restart.is_set():
            _restart.clear()
        for sym in list(_symbols) or settings.HERO_SYMBOLS:
            if sym not in base:
                rows = demo.klines(sym, "1m", 2)
                base[sym] = float(rows[-1][4])
            vol = 0.0006 if sym != "XAUUSDT" else 0.0002
            base[sym] *= 1 + random.gauss(0, vol)
            price = base[sym]
            spread = price * 0.00004
            _publish({
                "symbol": sym, "price": round(price, 8),
                "bid": round(price - spread, 8), "ask": round(price + spread, 8),
                "ts": int(time.time() * 1000), "demo": True,
                **_flow_fields(sym),
            })
        await asyncio.sleep(0.35)
      except asyncio.CancelledError:
        raise
      except Exception as exc:  # noqa: BLE001
        log.warning("demo tick dongusu hatasi (devam ediyor): %s", exc)
        await asyncio.sleep(1.0)


# --------------------------------------------------------------------------- #
# Canli Binance stream
# --------------------------------------------------------------------------- #
async def _rest_price_fallback(seconds: int = 90) -> None:
    """WS art arda koparsa terminali bayat fiyatla birakma.

    Karar motoru zaten mumlari REST'ten alir; bu yedek özellikle ekran ve
    açık pozisyonların anlık görünümü içindir. Süre dolunca WS yeniden
    denenir. Böylece bozuk bir bağlantıyı saniyede bir zorlayan döngü oluşmaz.
    """
    if _state.get("trades_connected"):
        # A healthy trade stream must not be overwritten with a cached REST price.
        await asyncio.sleep(seconds)
        return
    _state.update(connected=True, mode="rest-fallback")
    end = time.monotonic() + seconds
    log.warning("Binance WS devre kesici aktif — %ds REST fiyat yedegi", seconds)
    while time.monotonic() < end:
        syms = sorted(_symbols)[:24]

        async def one(sym: str) -> None:
            try:
                row = await binance.ticker_24h(sym)
                price = float(row.get("lastPrice") or row.get("price") or 0)
                if price <= 0:
                    return
                bid = float(row.get("bidPrice") or price)
                ask = float(row.get("askPrice") or price)
                _publish({"symbol": sym, "price": price, "bid": bid, "ask": ask,
                          "ts": int(time.time() * 1000), "demo": False,
                          "transport": "rest-fallback", **_flow_fields(sym)})
            except Exception as exc:  # noqa: BLE001
                log.debug("%s REST fiyat yedegi alinamadi: %s", sym, exc)

        await asyncio.gather(*(one(sym) for sym in syms))
        await asyncio.sleep(2.0)


async def _live_loop() -> None:
    import websockets

    backoff = 1.0
    disconnect_times = []
    while True:
        initial = (sorted(_symbols) or [s.upper() for s in settings.HERO_SYMBOLS])[:24]
        url = f"{settings.FSTREAM}/stream?streams=" + "/".join(s.lower()+"@bookTicker" for s in initial)
        try:
            # Binance sunucusu kendi ping frame'lerini yollar; websockets
            # bunlara otomatik, aynı payload ile pong döner. İstemci pingi
            # bazı ters proxy/NAT yollarında cevapsız kalıp sağlıklı veri
            # akışını gereksiz yere kesiyordu, bu yüzden kapalı. Akışın
            # gerçekten donmasını aşağıdaki payload zaman aşımı yakalar.
            async with websockets.connect(url, ping_interval=None,
                                          close_timeout=5, max_queue=512) as ws:
                _state.update(connected=True, mode="live")
                backoff = 1.0
                request_id = 1
                current = set(initial)
                request_id += 1
                _restart.clear()
                log.info("Binance WS bagli — %d sembol", len(current))
                last_payload = time.monotonic()
                while True:
                    if _restart.is_set():
                        _restart.clear()
                        desired = set(sorted(_symbols)[:24])
                        add = desired - current
                        remove = current - desired
                        if add:
                            await ws.send(json.dumps({"method": "SUBSCRIBE",
                                                      "params": [f"{s.lower()}@bookTicker" for s in sorted(add)],
                                                      "id": request_id}))
                            request_id += 1
                        if remove:
                            await ws.send(json.dumps({"method": "UNSUBSCRIBE",
                                                      "params": [f"{s.lower()}@bookTicker" for s in sorted(remove)],
                                                      "id": request_id}))
                            request_id += 1
                        current = desired
                        log.info("Binance WS aboneligi guncellendi — %d sembol (+%d/-%d)",
                                 len(current), len(add), len(remove))
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=45)
                    except asyncio.TimeoutError:
                        if time.monotonic() - last_payload > 90:
                            raise TimeoutError("90 saniyedir piyasa verisi gelmedi")
                        continue
                    last_payload = time.monotonic()
                    msg = json.loads(raw)
                    # SUBSCRIBE/UNSUBSCRIBE onayi: {"result": null, "id": n}
                    if "id" in msg and "result" in msg:
                        continue
                    d = msg.get("data", msg)
                    sym = d.get("s")
                    if not sym:
                        continue
                    bid, ask = float(d.get("b", 0)), float(d.get("a", 0))
                    if bid <= 0 or ask <= 0:
                        continue
                    _quotes[sym] = {"bid":bid,"ask":ask,"quote_ts":int(d.get("E") or time.time()*1000)}
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _state["connected"] = False
            _state["reconnects"] += 1
            now = time.monotonic()
            disconnect_times = [x for x in disconnect_times if now - x < 60]
            disconnect_times.append(now)
            log.warning("Binance WS koptu (%s) — %.1fs sonra yeniden", exc, backoff)
            if len(disconnect_times) >= 3:
                await _rest_price_fallback(90)
                disconnect_times.clear()
                backoff = 1.0
                continue
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            if binance.is_demo():
                return await _demo_loop()


async def _flow_poll_loop() -> None:
    """Futures aggTrade streami bolgesel olarak sessiz kalirsa dahi order-flow
    uret: acik 1m kline'in taker-buy hacmi Binance'in agresif alis ayrimidir."""
    while True:
        symbols = sorted(_symbols)[:12]

        async def one(sym: str) -> None:
            try:
                rows = await binance.klines(sym, "1m", 2)
                if not rows:
                    return
                row = rows[-1]
                price = float(row[4]); volume = float(row[5]); taker_buy = float(row[9])
                buy = taker_buy * price
                sell = max(volume - taker_buy, 0.0) * price
                _flows[sym] = {"buy": buy, "sell": sell, "started": float(row[0]) / 1000,
                               "last_emit": time.time()}
            except Exception as exc:  # noqa: BLE001
                log.debug("%s order-flow alinmadi: %s", sym, exc)

        await asyncio.gather(*(one(sym) for sym in symbols))
        await asyncio.sleep(1.0)


async def _trades_loop(shard):
    import websockets
    backoff = 1
    root = settings.FSTREAM.removesuffix("/public").removesuffix("/market")
    while True:
        try:
            current = set(sorted(_symbols)[:256][shard::4])
            if not current:
                await asyncio.sleep(2)
                continue
            streams = "/".join(s.lower()+suffix for s in sorted(current) for suffix in ("@aggTrade","@kline_15m"))
            if shard == 0:
                streams += "/!ticker@arr/!markPrice@arr@1s"
            async with websockets.connect(root + "/market/stream?streams="+streams, ping_interval=None, max_queue=1024) as ws:
                last_sync = 0
                while True:
                    if time.monotonic()-last_sync > 2:
                        desired = set(sorted(_symbols)[:256][shard::4])
                        if desired != current:
                            break
                        last_sync = time.monotonic()
                    try:
                        message = json.loads(await asyncio.wait_for(ws.recv(),timeout=10))
                    except asyncio.TimeoutError:
                        raise TimeoutError("Trade stream idle")
                    d = message.get("data",message)
                    if isinstance(d,list):
                        for item in d:
                            if item.get("s") not in _symbols:
                                continue
                            stats=_market_stats.setdefault(item["s"],{})
                            if item.get("e") == "24hrTicker":
                                stats.update(change_24h=float(item["P"]),volume_24h=float(item["q"]))
                            elif item.get("e") == "markPriceUpdate" and item.get("r") is not None:
                                stats["funding"]=float(item["r"])
                        continue
                    if d.get("e") == "kline":
                        k=d["k"]
                        _candles[d["s"]]=[k["t"],k["o"],k["h"],k["l"],k["c"],k["v"],k["T"]]
                        smc_rsi.advance_close(d['s'],k)
                        continue
                    if d.get("e") != "aggTrade":
                        continue
                    backoff = 1
                    symbol, timestamp = d["s"], int(d["T"])
                    previous = _ticks.get(symbol,{})
                    if timestamp < previous.get("ts",0):
                        continue
                    received = int(time.time()*1000)
                    _trade_connections.add(shard)
                    _state.update(trades_connected=True, last_trade_received=received)
                    _publish({"symbol":symbol,"price":float(d["p"]),"quantity":float(d["q"]),
                        "trade_id":d.get("a"),"ts":timestamp,"received_at":received,"demo":False,
                        "source":"aggTrade","candle":_candles.get(symbol),**_market_stats.get(symbol,{}),**_quotes.get(symbol,{}),**_flow_fields(symbol)})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _trade_connections.discard(shard)
            _state["trades_connected"] = bool(_trade_connections)
            log.warning("Trade stream reconnect: %s",type(exc).__name__)
            await asyncio.sleep(backoff)
            backoff = min(backoff*2,30)

async def _all_trades():
    await asyncio.gather(*(_trades_loop(i) for i in range(4)))

async def start() -> None:
    global _task, _flow_task, _trade_task
    if _task and not _task.done():
        return
    _symbols.update(s.upper() for s in settings.HERO_SYMBOLS)
    loop = _demo_loop if binance.is_demo() else _live_loop
    _task = asyncio.create_task(loop(), name="vortex-ws")
    if not binance.is_demo():
        from . import smc_orderbook
        await smc_orderbook.start()
        _trade_task = asyncio.create_task(_all_trades(), name="vortex-trades")
        _flow_task = asyncio.create_task(_flow_poll_loop(), name="vortex-order-flow")


async def stop() -> None:
    global _task, _flow_task, _trade_task
    from . import smc_orderbook
    await smc_orderbook.stop()
    if _trade_task:
        _trade_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _trade_task
        _trade_task = None
    if _flow_task:
        _flow_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _flow_task
    _flow_task = None
    if _task:
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
    _task = None
    _state.update(connected=False, mode="idle")
