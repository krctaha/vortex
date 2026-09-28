import copy
import json
import pytest
import numpy as np
from app import db
from app.services import smc_journal as j, smc_quality as q
from app.indicators import structure

def trade(**kw):
    return {"id":"a","symbol":"BTCUSDT","side":"LONG","state":"pending","entry":100.,"stop":95.,"target":110.,"created_at":61000,"checked_at":61000,**kw}

def bar(t,o=100,h=101,l=99,c=100):
    return [t,o,h,l,c,10,t+59999]

@pytest.fixture
def ledger(monkeypatch,tmp_path):
    monkeypatch.setattr(db.settings,"db_path",tmp_path/'paper.db')
    db.init();j.init()

def plan(**kw):
    return {"symbol":"BTCUSDT","side":"LONG","model":"MSS_FVG","entry":100.,"stop":95.,"target":110.,"ready":True,"demo":False,"rr":2.,"sequence":{},**kw}

def test_ignore_candle_that_started_before_observation():
    assert j.advance(trade(),[bar(60000,h=120,l=80)],200000)['state']=='pending'

def test_fill_then_target():
    result=j.advance(trade(),[bar(120000),bar(180000,o=103,h=111,l=101,c=110)],300000)
    assert result['state']=='target' and result['result_r']==2
    assert result['cost_r']<2

@pytest.mark.parametrize('h,l',[(112,99),(101,94),(112,94)])
def test_fill_candle_exit_sequence_is_ambiguous(h,l):
    result=j.advance(trade(),[bar(120000,h=h,l=l)],300000)
    assert result['state']=='ambiguous' and result.get('result_r') is None

def test_open_trade_both_levels_is_ambiguous():
    result=j.advance(trade(state='open',opened_at=120000,checked_at=179999),[bar(180000,h=112,l=90)],300000)
    assert result['state']=='ambiguous'

def test_gap_through_stop_not_optimistic():
    result=j.advance(trade(state='open',opened_at=120000,checked_at=179999),[bar(180000,o=90,h=93,l=89,c=92)],300000)
    assert result['result_r']==-2

def test_missing_bar_excluded():
    assert j.advance(trade(),[bar(180000)],300000)['state']=='data_gap'

def test_open_candle_excluded():
    assert j.advance(trade(),[bar(120000)],140000)['state']=='pending'

def test_short_fill_then_target():
    t=trade(side='SHORT',stop=105,target=90)
    result=j.advance(t,[bar(120000),bar(180000,o=98,h=99,l=89,c=90)],300000)
    assert result['state']=='target' and result['result_r']==2

def test_dedup_and_one_active_symbol(ledger):
    r=plan();j.register([r,r]);j.register([plan(entry=101)])
    assert db.query_one('SELECT COUNT(*) n FROM smc_paper')['n']==1
    j.register([plan(symbol='ETHUSDT',demo=True)])
    assert db.query_one('SELECT COUNT(*) n FROM smc_paper')['n']==1

def test_ranking_not_filter_tightening(ledger):
    rows=[plan(symbol=f'C{i}USDT') for i in range(30)]
    ranked=j.rank(rows)
    assert len(ranked)==30 and all(r['ready'] for r in ranked)
    assert sum(r['selected'] for r in ranked)==10
    assert all(r['verification']['learning_delta']==0 for r in ranked)

def test_learning_uses_closed_only_and_is_bounded(ledger):
    for i in range(80):
        r=plan(symbol=f'C{i}USDT');j.register([r])
        db.execute("UPDATE smc_paper SET state='target',cost_r=100,closed_at=100 WHERE symbol=?",(r['symbol'],))
    delta,n=j.feedback('MSS_FVG','LONG')
    assert delta==8 and n==80
    j.register([plan(symbol='OPENUSDT')])
    db.execute("UPDATE smc_paper SET cost_r=-100 WHERE symbol='OPENUSDT'")
    assert j.feedback('MSS_FVG','LONG')==(delta,n)

def test_order_block_changes_explanatory_score():
    base=plan(sequence={})
    good=copy.deepcopy(base);good['sequence']['order_block']={'bottom':99,'top':101,'mitigated_at':None}
    assert q.score(good)['score']-q.score(base)['score']==10
    assert sum(f['weight'] for f in q.score(good)['components'])==100
    assert q.score(good)['probability'] is False

def test_cost_guard_rejects_unrealistic_tiny_stop():
    r=plan(entry=1,stop=.99999,target=1.1)
    q.cost_guard(r)
    assert r['ready'] is False and r['phase']=='MALIYET_BEKLE'

def test_other_active_plan_not_selected_again(ledger):
    j.register([plan()])
    result=j.rank([plan(entry=101)])[0]
    assert result['selected'] is False and result['phase']=='TAKIPTE'

def test_cross_manual_records_scoped_to_owner(ledger):
    from app.api import terminal
    from fastapi import HTTPException
    r=terminal.create_cross(terminal.CrossTrade(asset='EURUSD',side='LONG',entry=1.1,quantity=10),{'id':1},None)
    assert not terminal.cross({'id':2})['items']
    with pytest.raises(HTTPException):
        terminal.close_cross(r['id'],terminal.CloseTrade(exit=1.2),{'id':2},None)
    terminal.close_cross(r['id'],terminal.CloseTrade(exit=1.2),{'id':1},None)
    assert terminal.cross({'id':1})['items'][0]['closed_at']

def test_order_block_mitigation_begins_after_confirmation(monkeypatch):
    class E:
        index=4
        direction='bull'
    monkeypatch.setattr(structure,'structure_events',lambda *a:[E()])
    o=np.array([10.,10.,10.,12.,15.,16.]);c=np.array([11.,9.,11.,13.,16.,17.])
    h=np.array([12.,11.,12.,14.,17.,18.]);l=np.array([9.,8.,7.,11.,14.,15.])
    block=structure.order_blocks(o,h,l,c)[0]
    assert block.confirmed_at==4 and block.mitigated_at is None
