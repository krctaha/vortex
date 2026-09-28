"""RSI(14), 15M: scanner candles seed Wilder averages; live trades vary the forming close.

No additional exchange requests and no changes to strategy selection.
Only a contiguous exchange-confirmed closing kline advances the baseline.
"""
import math
import threading

PERIOD=14
INTERVAL_MS=900_000
_states={}
_lock=threading.Lock()

def seed(rows):
    if len(rows)<PERIOD+1:return None
    closes=[float(r[4]) for r in rows]
    if any(not math.isfinite(p) or p<=0 for p in closes):return None
    changes=[b-a for a,b in zip(closes,closes[1:])]
    gain=sum(max(d,0) for d in changes[:PERIOD])/PERIOD
    loss=sum(max(-d,0) for d in changes[:PERIOD])/PERIOD
    for d in changes[PERIOD:]:
        gain=(gain*(PERIOD-1)+max(d,0))/PERIOD
        loss=(loss*(PERIOD-1)+max(-d,0))/PERIOD
    return {'period':PERIOD,'bar_open':int(rows[-1][0]),'as_of':int(rows[-1][6]),
            'close':closes[-1],'gain':gain,'loss':loss}

def install(symbol,value):
    if not value or value.get('period')!=PERIOD:return
    if any(not math.isfinite(float(value.get(k,float('nan')))) for k in ('close','gain','loss','bar_open','as_of')):return
    if value['close']<=0 or value['gain']<0 or value['loss']<0:return
    with _lock:
        previous=_states.get(symbol)
        if not previous or value['bar_open']>=previous['bar_open']:
            _states[symbol]=dict(value)

def advance_close(symbol,kline):
    if not kline.get('x'):return
    bar=int(kline['t']);close=float(kline['c'])
    if not math.isfinite(close) or close<=0:return
    with _lock:
        previous=_states.get(symbol)
        if not previous or bar<=previous['bar_open']:return
        if bar!=previous['bar_open']+INTERVAL_MS:
            _states.pop(symbol,None)  # Missing closed candles must be reseeded, not guessed.
            return
        d=close-previous['close']
        _states[symbol]={**previous,'bar_open':bar,'as_of':int(kline['T']),'close':close,
                        'gain':(previous['gain']*(PERIOD-1)+max(d,0))/PERIOD,
                        'loss':(previous['loss']*(PERIOD-1)+max(-d,0))/PERIOD}

def value(symbol,price,timestamp):
    with _lock:previous=dict(_states.get(symbol) or {})
    if not previous or not previous['as_of']<timestamp<=previous['as_of']+INTERVAL_MS:return None
    if not math.isfinite(price) or price<=0:return None
    d=price-previous['close']
    gain=(previous['gain']*(PERIOD-1)+max(d,0))/PERIOD
    loss=(previous['loss']*(PERIOD-1)+max(-d,0))/PERIOD
    if loss==0:return 100.0 if gain else 50.0
    return round(100-100/(1+gain/loss),2)

def radar(symbols,ticks,now):
    items=[]
    for symbol in symbols:
        tick=ticks.get(symbol,{})
        fresh=not tick.get('demo') and tick.get('source')=='aggTrade' and 0<=now-tick.get('ts',0)<10_000
        rsi=value(symbol,float(tick.get('price') or 0),tick.get('ts',0)) if fresh else None
        items.append({'symbol':symbol,'value':rsi,'ts':tick.get('ts') if rsi is not None else None})
    return {'period':PERIOD,'interval':'15m','forming':True,'items':items}
