import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import config, payments, scheduler, search
from app.db import connect, init_db
from app.main import app


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / 'db.sqlite')
    init_db(path)
    monkeypatch.setattr(config, 'DB_PATH', path)
    monkeypatch.setattr(config, 'ALLOW_ANON', True)
    monkeypatch.setattr(config, 'SCHEDULER_ENABLED', False)
    return path


def test_financial_validation_and_production_permissions(env, monkeypatch):
    client = TestClient(app)
    shop = client.post('/api/shops', json={'name': 'test'}).json()['shop_id']
    for count in (-3, 0, 10001):
        assert client.post('/api/payments/create', json={'kind': 'card_pack', 'shop_id': shop, 'count': count}).status_code in (400, 422)
    assert client.post('/api/orders', json={'shop_id': shop, 'amount': -1}).status_code == 422
    monkeypatch.setattr(config, 'ALLOW_ANON', False)
    assert client.post('/api/orders', json={'shop_id': shop, 'amount': 100}).status_code == 403
    assert client.post('/api/gifts', json={'recipient_name':'test','scene_ids':[1], 'consent':True}).status_code == 401
    monkeypatch.setattr(config, 'WEBHOOK_SECRET', '')
    assert client.post('/tg/webhook', json={}).status_code == 403


def test_single_card_is_claimed_once_concurrently(env):
    client = TestClient(app)
    shop = client.post('/api/shops', json={'name': 'test'}).json()['shop_id']
    code = client.post(f'/api/shops/{shop}/cards', json={'count':1}).json()['codes'][0]
    def create(_):
        return client.post('/api/gifts', json={'recipient_name':'X', 'scene_ids':[1], 'card_code':code}).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(create, range(4)))
    assert sorted(statuses) == [200, 409, 409, 409]
    conn = connect(env)
    assert conn.execute('SELECT count(*) FROM gifts WHERE card_id IS NOT NULL').fetchone()[0] == 1
    assert conn.execute('SELECT card_balance FROM shops WHERE id=?', (shop,)).fetchone()[0] == 0
    conn.close()


def test_concurrent_open_queues_one_notification(env):
    client = TestClient(app)
    gid = client.post('/api/gifts', json={'recipient_name':'X','scene_ids':[1], 'giver_telegram_id':12345}).json()['gift_id']
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: client.get('/api/gifts/' + gid).status_code, range(4))) == [200] * 4
    conn = connect(env)
    assert conn.execute("SELECT count(*) FROM notifications WHERE kind='gift_opened'").fetchone()[0] == 1
    assert conn.execute('SELECT view_count FROM gifts WHERE id=?', (gid,)).fetchone()[0] == 4
    conn.close()


def test_series_and_search(env):
    client = TestClient(app)
    assert client.post('/api/gifts', json={'recipient_name':'X','scene_ids':[1,2,3], 'format':'series','series_days':2}).status_code == 400
    gid = client.post('/api/gifts', json={'recipient_name':'X','scene_ids':[1,2], 'format':'series','series_days':2}).json()['gift_id']
    conn = connect(env)
    recent = datetime.now(timezone.utc) - timedelta(hours=2)
    conn.execute('UPDATE gifts SET created_at=?,open_at=? WHERE id=?', ((recent-timedelta(days=10)).isoformat(),recent.isoformat(),gid))
    conn.commit(); conn.close()
    data = client.get('/api/gifts/'+gid).json()
    assert len(data['scenes']) == 1 and data['locked_count'] == 1
    assert client.post('/api/scenes/semantic', json={'query':'любовь','limit':50}).status_code == 200
    conn = connect(env)
    conn.execute('UPDATE scenes SET popularity=100000 WHERE id=1'); conn.commit(); conn.close()
    assert client.post('/api/scenes/search', json={'limit':1}).json()['scenes'][0]['id'] == 1


def test_search_cache_invalidates_without_count_change(env):
    conn = connect(env)
    search.semantic_search(conn, 'unusualuniqueword')
    conn.execute("UPDATE scenes SET description='unusualuniqueword' WHERE id=1")
    conn.commit()
    assert search.semantic_search(conn, 'unusualuniqueword')[0]['scene_id'] == 1
    conn.close()


def test_reminders_cross_year(env, monkeypatch):
    monkeypatch.setattr(scheduler, 'now', lambda: datetime(2026, 12, 29, tzinfo=timezone.utc))
    conn = connect(env)
    conn.execute('INSERT INTO users(telegram_id) VALUES(7)')
    uid = conn.execute('SELECT id FROM users WHERE telegram_id=7').fetchone()[0]
    conn.execute("INSERT INTO important_dates(user_id,name,date,remind_days) VALUES(?,'test','01-01',7)", (uid,))
    scheduler._remind_dates(conn); scheduler._remind_dates(conn)
    assert conn.execute('SELECT count(*) FROM notifications').fetchone()[0] == 1
    assert conn.execute('SELECT notify_year FROM important_dates').fetchone()[0] == 2027
    conn.close()


def test_paid_periods_accumulate(env):
    conn = connect(env)
    conn.execute('INSERT INTO users(id,telegram_id) VALUES(9000,10)')
    conn.execute("INSERT INTO shops(id,name) VALUES(9000,'shop')")
    for i, kind, uid, sid in [('a','premium_film',9000,None), ('b','premium_film',9000,None), ('c','subscription',None,9000), ('d','subscription',None,9000)]:
        conn.execute("INSERT INTO payments(id,user_id,shop_id,kind,payload,amount_rub) VALUES(?,?,?,?, '{}',1)", (i,uid,sid,kind))
    conn.commit()
    for i in 'abcd': payments.apply_payment(conn, {'id':i})
    premium = datetime.fromisoformat(conn.execute('SELECT premium_until FROM users WHERE id=9000').fetchone()[0])
    subscription = datetime.fromisoformat(conn.execute('SELECT subscribed_until FROM shops WHERE id=9000').fetchone()[0])
    assert premium > datetime.now(timezone.utc) + timedelta(days=729)
    assert subscription > datetime.now(timezone.utc) + timedelta(days=59)
    conn.close()


def test_provider_create_recovery_reuses_persisted_key(env, monkeypatch):
    monkeypatch.setattr(config, 'YUKASSA_SHOP_ID', '123')
    monkeypatch.setattr(config, 'YUKASSA_SECRET_KEY', 'secret')
    keys = []
    class Response:
        async def __aenter__(self):
            if len(keys) == 1: raise TimeoutError()
            return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def json(self): return {'id':'remote-id', 'confirmation':{'confirmation_url':'https://pay.example/'}}
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def post(self, url, *, json, headers):
            keys.append(headers['Idempotence-Key'])
            conn = connect(env)
            assert conn.execute('SELECT status FROM payments WHERE id=?', (keys[-1],)).fetchone()[0] == 'creating'
            conn.close()
            return Response()
    monkeypatch.setattr(payments.aiohttp, 'ClientSession', Session)
    conn = connect(env); conn.execute('INSERT INTO users(id,telegram_id) VALUES(9000,1)'); conn.commit(); conn.close()
    result = asyncio.run(payments.create_payment('premium_film', user_id=9000, base_url='https://scena.test'))
    assert result['status'] == 'creating'
    asyncio.run(payments.recover_creating_payments())
    assert keys == [result['payment_id']] * 2
    assert payments.get_payment(result['payment_id'])['provider_payment_id'] == 'remote-id'
    assert payments.get_payment(result['payment_id'])['status'] == 'pending'


def test_reconcile_terminal_payment_after_verification_failure(env, monkeypatch):
    """A crash/outage after saving a terminal provider response must be recoverable."""
    monkeypatch.setattr(config, 'YUKASSA_SHOP_ID', '123')
    monkeypatch.setattr(config, 'YUKASSA_SECRET_KEY', 'secret')
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def json(self): return {'id':'provider-1', 'status':'succeeded'}
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def post(self, url, **kwargs): return Response()
    monkeypatch.setattr(payments.aiohttp, 'ClientSession', Session)
    calls = []
    async def verify(event):
        calls.append(event)
        if len(calls) == 1:
            raise TimeoutError('provider unavailable')
        return {'event':'payment.succeeded','object':{'id':'provider-1','metadata':{'payment_id':pid}}}
    monkeypatch.setattr(payments, 'verify_provider_event', verify)
    conn = connect(env)
    conn.execute('INSERT INTO users(id,telegram_id) VALUES(9000,10000)')
    conn.commit(); conn.close()
    result = asyncio.run(payments.create_payment('premium_film', user_id=9000, base_url='https://scena.test'))
    pid = result['payment_id']
    assert result['status'] == 'verifying_succeeded'
    asyncio.run(payments.recover_creating_payments())
    assert payments.get_payment(pid)['status'] == 'paid'
    conn = connect(env)
    paid_until = conn.execute('SELECT premium_until FROM users WHERE id=9000').fetchone()[0]
    conn.close()
    asyncio.run(payments.recover_creating_payments())
    conn = connect(env)
    assert conn.execute('SELECT premium_until FROM users WHERE id=9000').fetchone()[0] == paid_until
    conn.close()


def test_delete_open_and_reply_are_serialized(env):
    client = TestClient(app)
    for index in range(5):
        gid = client.post('/api/gifts', json={'recipient_name':'X','scene_ids':[1], 'giver_telegram_id':90000 + index}).json()['gift_id']
        def request(action):
            if action == 'delete':
                return client.post(f'/api/gifts/{gid}/delete?telegram_id={90000 + index}').status_code
            if action == 'open':
                return client.get(f'/api/gifts/{gid}').status_code
            return client.post(f'/api/gifts/{gid}/reply', json={'type':'reaction','reaction':'x'}).status_code
        with ThreadPoolExecutor(max_workers=3) as pool:
            outcomes = list(pool.map(request, ('delete', 'open', 'reply')))
        assert outcomes[0] == 200
        assert all(code in (200, 404) for code in outcomes)
        conn = connect(env)
        assert conn.execute('SELECT status FROM gifts WHERE id=?', (gid,)).fetchone()[0] == 'deleted'
        conn.close()
        assert client.get(f'/api/gifts/{gid}').status_code == 404


def test_snapshot_zero_and_changed_direct_link(env):
    client = TestClient(app)
    conn = connect(env)
    conn.execute("UPDATE scenes SET timecode_label='0:00',timecode_seconds=0,watch_url='https://example.com/old' WHERE id=1")
    conn.commit(); conn.close()
    gid = client.post('/api/gifts', json={'recipient_name':'X','scene_ids':[1]}).json()['gift_id']
    conn = connect(env)
    conn.execute("UPDATE scenes SET timecode_label='1:00',timecode_seconds=60,watch_url='https://example.com/new' WHERE id=1")
    conn.commit(); conn.close()
    scene = client.get('/api/gifts/' + gid).json()['scenes'][0]
    assert scene['timecode_seconds'] == 0
    assert scene['timecode_label'] == '0:00'
    assert scene['watch_url'] is None
    assert not any(link['direct'] for link in scene['watch_links'])


def test_dev_confirmation_rejects_provider_payment(env):
    conn = connect(env)
    conn.execute("INSERT INTO payments(id,kind,payload,amount_rub,status,provider) VALUES('real','premium_film','{}',499,'pending','yookassa')")
    conn.commit(); conn.close()
    assert payments.confirm_dev('real') is None
    assert payments.get_payment('real')['status'] == 'pending'


def _signed_data(token, user):
    import hashlib, hmac, json, time
    from urllib.parse import urlencode
    data = {'auth_date': str(int(time.time())), 'user': json.dumps(user)}
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    data['hash'] = hmac.new(secret, '\n'.join(f'{k}={v}' for k, v in sorted(data.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def test_concurrent_new_telegram_user_and_malformed_data(env, monkeypatch):
    from app.security import upsert_user_from_init_data, validate_init_data
    monkeypatch.setattr(config, 'BOT_TOKEN', 'test-token')
    signed = _signed_data(config.BOT_TOKEN, {'id': 445566, 'first_name': 'User'})
    with ThreadPoolExecutor(max_workers=8) as pool:
        users = list(pool.map(lambda _: upsert_user_from_init_data(signed), range(20)))
    assert len({u['id'] for u in users}) == 1
    conn = connect(env)
    assert conn.execute('SELECT COUNT(*) FROM users WHERE telegram_id=445566').fetchone()[0] == 1
    conn.close()
    assert validate_init_data('hash=%C3%A9', config.BOT_TOKEN) is None
    assert validate_init_data(signed + '&hash=duplicate', config.BOT_TOKEN) is None
    for user in ([], {'id': True}, {'id': -1}, {'id': 2**63}, {'id': 1, 'first_name': []}):
        assert upsert_user_from_init_data(_signed_data(config.BOT_TOKEN, user)) is None


def test_gift_creation_rolls_back_card_consent_and_event(env, monkeypatch):
    import app.main as main
    client = TestClient(app, raise_server_exceptions=False)
    shop = client.post('/api/shops', json={'name':'test'}).json()['shop_id']
    code = client.post(f'/api/shops/{shop}/cards', json={'count':1}).json()['codes'][0]
    def fail(*args, **kwargs): raise RuntimeError('simulated database failure')
    monkeypatch.setattr(main, 'track_event', fail)
    assert client.post('/api/gifts', json={'recipient_name':'X','scene_ids':[1],'card_code':code}).status_code == 500
    conn = connect(env)
    assert conn.execute('SELECT status FROM cards WHERE code=?', (code,)).fetchone()[0] == 'new'
    assert conn.execute('SELECT card_balance FROM shops WHERE id=?', (shop,)).fetchone()[0] == 1
    assert conn.execute('SELECT count(*) FROM gifts WHERE card_id IS NOT NULL').fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM consents').fetchone()[0] == 0
    conn.close()


def test_reply_before_open_sets_opened_state_and_transactional_notifications(env, monkeypatch):
    import app.main as main
    client = TestClient(app, raise_server_exceptions=False)
    gid = client.post('/api/gifts', json={'recipient_name':'X','scene_ids':[1], 'giver_telegram_id':777777}).json()['gift_id']
    original = main.log_consent
    def fail(*args, **kwargs): raise RuntimeError('simulated failure')
    monkeypatch.setattr(main, 'log_consent', fail)
    assert client.post(f'/api/gifts/{gid}/reply', json={'type':'reaction','reaction':'x'}).status_code == 500
    conn = connect(env)
    assert conn.execute('SELECT status FROM gifts WHERE id=?', (gid,)).fetchone()[0] == 'created'
    assert conn.execute('SELECT count(*) FROM replies WHERE gift_id=?', (gid,)).fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM notifications').fetchone()[0] == 0
    conn.close()
    monkeypatch.setattr(main, 'log_consent', original)
    assert client.post(f'/api/gifts/{gid}/reply', json={'type':'reaction','reaction':'x'}).status_code == 200
    assert client.get(f'/api/gifts/{gid}').json()['gift']['opened_at'] is not None
    conn = connect(env)
    assert conn.execute("SELECT COUNT(*) FROM events WHERE ref=? AND kind='gift_opened'", (gid,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM notifications WHERE kind='gift_opened'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM notifications WHERE kind='reply'").fetchone()[0] == 1
    conn.close()


def test_payment_amount_is_rounded_before_provider_verification(env, monkeypatch):
    monkeypatch.setattr(config, 'PRICE_PREMIUM_FILM', 499.995)
    assert payments.amount_for('premium_film') == 500.00
    monkeypatch.setattr(config, 'PRICE_PREMIUM_FILM', 0)
    with pytest.raises(ValueError):
        payments.amount_for('premium_film')
