import copy
import time
import pytest
from app.services import smc_orderbook as book, binance_ws

def payload():
    return {'e':'depthUpdate','s':'BTCUSDT','E':int(time.time()*1000),'u':100,
            'b':[['99','2'],['98','4']],'a':[['100','3'],['101','5']]}

@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(book,'_book',None)
    monkeypatch.setattr(binance_ws,'_subscribers',set())
    monkeypatch.setattr(binance_ws,'_ticks',{'BTCUSDT':{'price':99.5,'ts':1}})

def test_full_partial_snapshot_replaces_not_merges_and_preserves_trade_price():
    original=copy.deepcopy(binance_ws.snapshot());q=binance_ws.subscribe()
    assert book.ingest(payload())
    assert q.get_nowait()['type']=='depth'
    assert binance_ws.snapshot()==original
    updated={**payload(),'u':101,'b':[['97','7']],'a':[['100','0'],['102','6']]}
    assert book.ingest(updated)
    assert book.snapshot()['bids']==[[97.,7.]]
    assert book.snapshot()['asks']==[[102.,6.]]

def test_out_of_order_updates_rejected():
    assert book.ingest(payload())
    assert not book.ingest({**payload(),'u':99})
    assert not book.ingest(payload())
    assert book.snapshot()['update_id']==100

@pytest.mark.parametrize('field,value',[('s','ETHUSDT'),('e','aggTrade'),('b',[['100','2']]),
    ('b',[['NaN','2']]),('a',[['101','-1']]),('b',[['99','1'],['99','2']]),('a',[]),('E',0)])
def test_bad_book_rejected(field,value):
    assert book.normalize({**payload(),field:value}) is None

def test_stale_and_disconnected_books_not_presented_as_live():
    assert book.ingest({**payload(),'E':int(time.time()*1000)-2001})
    assert book.snapshot() is None
    q=binance_ws.subscribe();book.clear()
    assert book.snapshot() is None
    event=q.get_nowait()
    assert event['source']=='disconnected' and event['bids']==[]

def test_sorts_and_bounds_depth_to_twenty():
    d={**payload(),'b':[[str(99-i),str(i+1)] for i in reversed(range(30))],
       'a':[[str(100+i),'2'] for i in reversed(range(30))]}
    normalized=book.normalize(d)
    assert len(normalized['bids'])==len(normalized['asks'])==20
    assert normalized['bids'][0]==[99.,1.]
    assert normalized['asks'][0]==[100.,2.]
