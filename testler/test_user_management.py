import pytest
import httpx
from app import db, security
from app.main import app

@pytest.fixture
def accounts(monkeypatch,tmp_path):
    monkeypatch.setattr(db.settings,'db_path',tmp_path/'users.db');db.init()
    for uid,name,role in [(1,'owner','admin'),(2,'member','user')]:
        db.execute('INSERT INTO users(id,username,password_hash,role,created_at) VALUES(?,?,?,?,?)',
                   (uid,name,security.hash_password('test-password-only'),role,db.now_ms()))

def client(uid):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
        cookies={'vortex_session':security.make_session({'uid':uid})})

@pytest.mark.asyncio
@pytest.mark.parametrize('method,path,body',[
    ('GET','/api/users',None),('POST','/api/users',{'username':'other','password':'test-password-only'}),
    ('DELETE','/api/users/1',None),('POST','/api/users/1/active',{'is_active':False}),
    ('POST','/api/users/1/role',{'role':'user'}),('POST','/api/users/1/password',{'new_password':'test-password-only'})])
async def test_member_cannot_manage_accounts(accounts,method,path,body):
    async with client(2) as c:
        r=await c.request(method,path,json=body)
        assert r.status_code==403
    assert db.query_one('SELECT count(*) c FROM users')['c']==2

@pytest.mark.asyncio
async def test_management_markup_is_admin_only(accounts):
    async with client(2) as c:
        r=await c.get('/ayarlar');assert r.status_code==200
        assert 'id="usersPanel"' not in r.text and 'id="userCreateForm"' not in r.text
    async with client(1) as c:
        r=await c.get('/ayarlar');assert 'id="usersPanel"' in r.text

@pytest.mark.asyncio
async def test_create_account_hashes_password_and_hides_secrets(accounts):
    async with client(1) as c:
        r=await c.post('/api/users',json={'username':'new-user','password':'test-password-only','display_name':'New'})
        assert r.status_code==200 and r.json()['user']['role']=='user'
        assert 'password_hash' not in r.text and 'telegram_token' not in r.text
        listing=await c.get('/api/users');assert 'password_hash' not in listing.text
    row=db.query_one("SELECT * FROM users WHERE username='new-user'")
    assert row['password_hash']!='test-password-only' and security.verify_password('test-password-only',row['password_hash'])

@pytest.mark.asyncio
async def test_owner_cannot_delete_or_disable_self(accounts):
    async with client(1) as c:
        assert (await c.delete('/api/users/1?purge=true')).status_code==400
        assert (await c.post('/api/users/1/active',json={'is_active':False})).status_code==400
        assert (await c.post('/api/users/1/role',json={'role':'user'})).status_code==400
    assert db.query_one('SELECT role,is_active FROM users WHERE id=1')=={'role':'admin','is_active':1}

@pytest.mark.asyncio
async def test_delete_requires_record_purge_and_cleans_private_settings(accounts):
    db.execute('INSERT INTO watchlist(user_id,symbol,added_at) VALUES(?,?,?)',(2,'BTCUSDT',db.now_ms()))
    db.set_setting('smc_notifications_2',{'enabled':True})
    async with client(1) as c:
        listing=(await c.get('/api/users')).json();member=next(u for u in listing['items'] if u['id']==2)
        assert member['usage']['watchlist']==1
        assert (await c.delete('/api/users/2')).status_code==409
        assert db.query_one('SELECT id FROM users WHERE id=2')
        r=await c.delete('/api/users/2?purge=true');assert r.status_code==200
        assert r.json()['deleted']['watchlist']==1
    assert not db.query_one('SELECT id FROM users WHERE id=2')
    assert not db.query_one('SELECT user_id FROM watchlist WHERE user_id=2')
    assert db.get_setting('smc_notifications_2') is None
    async with client(2) as c:assert (await c.get('/api/users')).status_code==401

@pytest.mark.asyncio
async def test_disabling_revokes_existing_session(accounts):
    async with client(1) as c:
        assert (await c.post('/api/users/2/active',json={'is_active':False})).status_code==200
    async with client(2) as c:assert (await c.get('/api/terminal/preferences')).status_code==403

@pytest.mark.asyncio
async def test_spoofed_origin_cannot_mutate_users(accounts):
    async with client(1) as c:
        r=await c.post('/api/users',headers={'Origin':'https://evil.example/?test'},json={'username':'blocked','password':'test-password-only'})
        assert r.status_code==403
    assert not db.query_one("SELECT id FROM users WHERE username='blocked'")
