import asyncio
import copy
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app import db
from app.services import smc_notifications as n, smc_journal as j, premium_engine as engine, binance_ws, telegram
from app.api import misc, terminal
from app.services import smc_board

def plan(symbol="BTCUSDT",side="LONG"):
    return dict(symbol=symbol,side=side,model="MSS_FVG",entry=100.,stop=95. if side=="LONG" else 105.,
        target=110. if side=="LONG" else 90.,rr=2.,ready=True,selected=True,demo=False,
        verification={"score":85.,"learning_delta":1.,"components":[{"name":"FVG","value":1}]},sequence={})

@pytest.fixture
def setup(monkeypatch,tmp_path):
    monkeypatch.setattr(db.settings,"db_path",tmp_path/'notify.db');db.init();n.init();j.init()
    db.execute("INSERT INTO users(id,username,password_hash,role,created_at,telegram_token,telegram_chat_id) VALUES(1,'qa','hash','admin',1,'token','chat')")
    db.set_setting('smc_notifications_1',{**n.DEFAULTS,'enabled':True,'instant_enabled':False})
    now=2_000_000_000_000
    monkeypatch.setattr(db,'now_ms',lambda:now)
    scanner={'enabled':True,'demo':False,'stale':False,'scanning':False,'last_scan_at':now-1000,'scanned':200,'candidates':[plan()],'results':[plan()]}
    monkeypatch.setattr(engine,'status',lambda:scanner)
    monkeypatch.setattr(binance_ws,'snapshot',lambda:{'BTCUSDT':{'price':100,'ts':now},'ETHUSDT':{'price':100,'ts':now}})
    sent=[]
    async def send(text,**kw):sent.append(text);return {'ok':True}
    monkeypatch.setattr(telegram,'send',send)
    return scanner,sent,now

@pytest.mark.asyncio
async def test_real_delivery_and_persistent_dedup(setup):
    scanner,sent,now=setup
    await n.run_due();await n.run_due()
    assert len(sent)==1 and 'FVG' in sent[0]
    assert n.state(1)['last_sent']==now
    db.set_setting('smc_notification_state_1',{})
    scanner['last_scan_at']+=1
    await n.run_due()
    assert len(sent)==2 and 'Yeni, tekrar' in sent[1]
    assert db.query_one('SELECT count(*) c FROM smc_notification_log')['c']==1

@pytest.mark.asyncio
@pytest.mark.parametrize('field',['demo','stale','scanning'])
async def test_unsafe_scan_not_sent(setup,field):
    scanner,sent,_=setup;scanner[field]=True
    await n.run_due();assert not sent

@pytest.mark.asyncio
async def test_disabled_user_no_send(setup):
    _,sent,_=setup;db.set_setting('smc_notifications_1',{'enabled':False})
    await n.run_due();assert not sent

def test_direction_restricts_selection_not_shadow_learning(setup):
    db.set_setting('smc_direction','SHORT')
    rows=j.rank([plan(),plan('ETHUSDT','SHORT')])
    assert next(r for r in rows if r['side']=='LONG')['selected'] is False
    assert next(r for r in rows if r['side']=='LONG')['ready'] is True
    assert next(r for r in rows if r['side']=='SHORT')['selected'] is True
    db.set_setting('smc_direction','AUTO')
    assert all(r['selected'] for r in j.rank([plan(),plan('ETHUSDT','SHORT')]))

def test_same_symbol_different_setup_suppressed(setup):
    scanner,_,now=setup
    db.execute('INSERT INTO smc_notification_log VALUES(?,?,?,?,?)',(1,'old','BTCUSDT',now-1000,'sent'))
    assert not n.pick(1,scanner,n.preferences(1),now)

@pytest.mark.asyncio
async def test_failed_delivery_not_claimed_success_or_retried(setup,monkeypatch):
    calls=[]
    async def fail(*a,**kw):calls.append(1);return {'ok':False,'error':'Forbidden'}
    monkeypatch.setattr(telegram,'send',fail)
    await n.run_due();await n.run_due()
    assert len(calls)==1
    assert n.state(1)['error']=='Forbidden' and not n.state(1).get('last_sent')
    assert db.query_one('SELECT state FROM smc_notification_log')['state']=='unconfirmed'

@pytest.mark.asyncio
async def test_simultaneous_scheduler_runs_send_once(setup):
    _,sent,_=setup
    await asyncio.gather(n.run_due(),n.run_due())
    assert len(sent)==1

@pytest.mark.asyncio
async def test_telegram_test_checks_actual_delivery(monkeypatch):
    async def valid(*a):return {'ok':True,'bot':'qa'}
    async def fail(*a,**kw):return {'ok':False,'error':'chat not found'}
    monkeypatch.setattr(telegram,'test_connection',valid);monkeypatch.setattr(telegram,'send',fail)
    with pytest.raises(HTTPException) as exc:
        await misc.telegram_test({'telegram_token':'token','telegram_chat_id':'chat'})
    assert exc.value.status_code==502

def test_preferences_never_return_token(setup):
    user=db.query_one('SELECT * FROM users WHERE id=1')
    result=terminal.preferences(user)
    assert 'telegram_token' not in result['telegram'] and result['telegram']['token_saved'] is True

@pytest.mark.parametrize('body',[{'direction':'BAD'},{'enabled':True,'interval_minutes':1}])
def test_invalid_settings_rejected(body):
    with pytest.raises(ValidationError):
        (terminal.DirectionBody if 'direction' in body else terminal.NotificationBody)(**body)

def prepare_instant(setup):
    scanner,sent,now=setup
    db.set_setting('smc_notifications_1',{**n.DEFAULTS,'enabled':True,'instant_enabled':True})
    db.set_setting('smc_instant_since_1',now-60000)
    db.set_setting('smc_notification_state_1',{'last_attempt':now,'scan_at':scanner['last_scan_at']})
    j.register(scanner['candidates'])
    return scanner,sent,now

@pytest.mark.asyncio
async def test_instant_does_not_wait_for_hour_or_coin_cooldown(setup,monkeypatch):
    scanner,sent,now=prepare_instant(setup)
    monkeypatch.setattr(binance_ws,'snapshot',lambda:{'BTCUSDT':{'price':101,'ts':now}})
    db.execute('INSERT INTO smc_notification_log VALUES(?,?,?,?,?)',(1,'previous-setup','BTCUSDT',now-1000,'sent'))
    await n.run_due();await n.run_due()
    assert len(sent)==1 and 'YENİ GİRİŞ PLANI' in sent[0]
    assert n.state(1)['last_instant_sent']==now

@pytest.mark.asyncio
@pytest.mark.parametrize('reason',['open','crossed','old','stale_quote','unconfirmed'])
async def test_no_late_instant_signals(setup,monkeypatch,reason):
    scanner,sent,now=prepare_instant(setup)
    monkeypatch.setattr(binance_ws,'snapshot',lambda:{'BTCUSDT':{'price':101,'ts':now}})
    if reason=='open':db.execute("UPDATE smc_paper SET state='open'")
    elif reason=='crossed':monkeypatch.setattr(binance_ws,'snapshot',lambda:{'BTCUSDT':{'price':99,'ts':now}})
    elif reason=='old':db.execute('UPDATE smc_paper SET created_at=?',(now-600000,))
    elif reason=='stale_quote':monkeypatch.setattr(binance_ws,'snapshot',lambda:{'BTCUSDT':{'price':101,'ts':now-20000}})
    else:scanner['candidates'][0]['ready']=False
    await n.run_due();assert not sent

@pytest.mark.asyncio
async def test_enabling_does_not_rebroadcast_existing_records(setup,monkeypatch):
    scanner,sent,now=prepare_instant(setup)
    db.execute("DELETE FROM app_settings WHERE key='smc_instant_since_1'")
    db.execute('UPDATE smc_paper SET created_at=?',(now-1000,))
    monkeypatch.setattr(binance_ws,'snapshot',lambda:{'BTCUSDT':{'price':101,'ts':now}})
    await n.run_due();assert not sent

def test_board_keeps_open_and_closed_visible_without_new_opportunities(setup,monkeypatch):
    scanner,_,now=setup
    j.register([plan(),plan('ETHUSDT')])
    db.execute("UPDATE smc_paper SET state='open',opened_at=? WHERE symbol='BTCUSDT'",(now-1000,))
    db.execute("UPDATE smc_paper SET state='stop',closed_at=?,cost_r=-1.02 WHERE symbol='ETHUSDT'",(now,))
    scanner['candidates']=[]
    board=smc_board.snapshot(scanner)
    assert not board['pending']
    assert len(board['open'])==1 and board['today']['stop']==1
    assert 'yeni giriş fırsatı değil' in board['open'][0]['entry_note']

def test_board_and_instant_share_entry_guard(setup,monkeypatch):
    scanner,_,now=prepare_instant(setup)
    monkeypatch.setattr(binance_ws,'snapshot',lambda:{'BTCUSDT':{'price':99,'ts':now}})
    board=smc_board.snapshot(scanner)
    assert board['pending'][0]['actionable'] is False
    assert not n.pick(1,scanner,n.preferences(1),now,instant=True)
