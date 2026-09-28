import math
import pytest
from app.services import smc_rsi as r

def bars(closes,start=0):
    return [[start+i*r.INTERVAL_MS,p,p,p,p,1,start+(i+1)*r.INTERVAL_MS-1] for i,p in enumerate(closes)]

@pytest.fixture(autouse=True)
def isolated(monkeypatch):monkeypatch.setattr(r,'_states',{})

@pytest.mark.parametrize('closes,expected',[(list(range(100,130)),100.0),(list(range(130,100,-1)),0.0),([100.0]*30,50.0)])
def test_trend_and_flat_boundaries(closes,expected):
    seed=r.seed(bars(closes));r.install('BTCUSDT',seed)
    assert r.value('BTCUSDT',closes[-1],seed['as_of']+1)==expected

def test_known_wilder_initial_value_and_forming_price_does_not_mutate_seed():
    prices=[44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28]
    seed=r.seed(bars(prices));r.install('BTCUSDT',seed)
    assert r.value('BTCUSDT',prices[-1],seed['as_of']+1)==pytest.approx(70.46,abs=.01)
    first=r.value('BTCUSDT',47,seed['as_of']+2)
    r.value('BTCUSDT',43,seed['as_of']+3)
    assert r.value('BTCUSDT',47,seed['as_of']+4)==first
    assert r._states['BTCUSDT']==seed

def test_exchange_close_matches_full_wilder_recalculation():
    prices=[100+math.sin(i)*5 for i in range(100)];rows=bars(prices)
    seed=r.seed(rows);r.install('BTCUSDT',seed)
    next_bar=seed['bar_open']+r.INTERVAL_MS
    k={'t':next_bar,'T':next_bar+r.INTERVAL_MS-1,'c':'102','x':True}
    r.advance_close('BTCUSDT',k)
    expected=r.seed(rows+bars([102],start=next_bar))
    assert r._states['BTCUSDT']==pytest.approx(expected)
    r.advance_close('BTCUSDT',k);assert r._states['BTCUSDT']==pytest.approx(expected)
    r.install('BTCUSDT',seed);assert r._states['BTCUSDT']==pytest.approx(expected)

def test_open_kline_does_not_advance_and_gap_requires_reseed():
    seed=r.seed(bars(list(range(100,130))));r.install('BTCUSDT',seed)
    r.advance_close('BTCUSDT',{'t':seed['bar_open']+r.INTERVAL_MS,'c':'131','x':False})
    assert r._states['BTCUSDT']==seed
    r.advance_close('BTCUSDT',{'t':seed['bar_open']+r.INTERVAL_MS*2,'T':seed['as_of']+r.INTERVAL_MS*2,'c':'131','x':True})
    assert r.value('BTCUSDT',131,seed['as_of']+1) is None

@pytest.mark.parametrize('offset',[0,r.INTERVAL_MS+1])
def test_stale_baseline_never_crosses_into_wrong_forming_bar(offset):
    seed=r.seed(bars(list(range(100,130))));r.install('BTCUSDT',seed)
    assert r.value('BTCUSDT',130,seed['as_of']+offset) is None

def test_radar_rejects_stale_demo_prices_and_unknown_seed():
    seed=r.seed(bars(list(range(100,130))));r.install('BTCUSDT',seed);now=seed['as_of']+1000
    tick={'source':'aggTrade','price':130,'ts':now,'demo':False}
    assert r.radar(['BTCUSDT'],{'BTCUSDT':tick},now)['items'][0]['value']==100
    assert r.radar(['BTCUSDT'],{'BTCUSDT':tick},now+10001)['items'][0]['value'] is None
    assert r.radar(['BTCUSDT'],{'BTCUSDT':{**tick,'demo':True}},now)['items'][0]['value'] is None
    assert r.radar(['ETHUSDT'],{'ETHUSDT':tick},now)['items'][0]['value'] is None

def test_invalid_seed_rejected():
    assert r.seed(bars([100]*10)) is None
    assert r.seed(bars([100]*14+[float('nan')])) is None
    r.install('BTCUSDT',{'period':14,'close':float('nan')})
    assert not r._states
