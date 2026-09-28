"""Shared BTC USD-M top-20 partial snapshots; never merge them as diff updates."""
import asyncio
import contextlib
import json
import logging
import math
import time
from ..config import settings

log = logging.getLogger('vortex.depth')
_book = None
_task = None

def normalize(data):
    if data.get('e') != 'depthUpdate' or data.get('s') != 'BTCUSDT':
        return None
    try:
        def levels(key, reverse):
            rows = [(float(p), float(q)) for p, q in data[key]]
            if not rows or any(not math.isfinite(p) or not math.isfinite(q) or p <= 0 or q < 0 for p, q in rows):
                raise ValueError('Invalid level')
            prices = [p for p, q in rows if q > 0]
            if len(prices) != len(set(prices)):
                raise ValueError('Duplicate level')
            return sorted(([p, q] for p, q in rows if q > 0), reverse=reverse)[:20]
        bids, asks = levels('b', True), levels('a', False)
        if not bids or not asks or bids[0][0] >= asks[0][0]:
            return None
        ts, update_id = int(data['E']), int(data['u'])
        if ts <= 0 or update_id <= 0:
            return None
        return {'type':'depth','symbol':'BTCUSDT','bids':bids,'asks':asks,
                'ts':ts,'update_id':update_id,'source':'depth20','demo':False}
    except (ValueError, TypeError, KeyError, OverflowError):
        return None

def ingest(data):
    global _book
    book = normalize(data)
    if not book or (_book and book['update_id'] <= _book['update_id']):
        return False
    _book = book
    from . import binance_ws
    binance_ws._emit(book)
    return True

def snapshot():
    return _book if _book and 0 <= time.time()*1000-_book['ts'] < 2000 else None

def clear():
    global _book
    _book = None
    from . import binance_ws
    binance_ws._emit({'type':'depth','symbol':'BTCUSDT','bids':[],'asks':[],
                      'ts':int(time.time()*1000),'source':'disconnected','demo':False})

async def _loop():
    import websockets
    root = settings.FSTREAM.removesuffix('/public').removesuffix('/market')
    delay = 1
    while True:
        try:
            async with websockets.connect(root+'/public/ws/btcusdt@depth20@100ms',ping_interval=None,max_queue=16) as ws:
                clear()
                while True:
                    data = json.loads(await asyncio.wait_for(ws.recv(),5))
                    if ingest(data):
                        delay = 1
        except asyncio.CancelledError:
            clear()
            raise
        except Exception as exc:
            clear()
            log.warning('BTC depth reconnect: %s',type(exc).__name__)
            await asyncio.sleep(delay)
            delay = min(30, delay*2)

async def start():
    global _task
    if settings.data_mode != 'demo' and (not _task or _task.done()):
        _task = asyncio.create_task(_loop(),name='vortex-btc-depth')

async def stop():
    global _task
    if _task:
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
        _task = None
