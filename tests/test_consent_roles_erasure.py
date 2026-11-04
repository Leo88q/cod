import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import connect, init_db
from app.main import app


def signed(uid):
    data = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid})}
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, '\n'.join(f'{k}={v}' for k,v in sorted(data.items())).encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(data)}


@pytest.fixture
def private_app(tmp_path, monkeypatch):
    path = str(tmp_path / 'private.db')
    init_db(path, with_seed=False)
    monkeypatch.setattr(config, 'DB_PATH', path)
    monkeypatch.setattr(config, 'ALLOW_ANON', False)
    monkeypatch.setattr(config, 'BOT_TOKEN', 'unit-test-token')
    conn = connect(path)
    for uid in (1,2,3):
        conn.execute('INSERT INTO users(id,telegram_id) VALUES(?,?)', (uid,100+uid))
    conn.execute("INSERT INTO shops(id,name,owner_user_id) VALUES(1,'Owned studio',1)")
    conn.commit()
    conn.close()
    # No lifespan: do not start a real Telegram bot in a unit test.
    return TestClient(app), path


def test_mutual_connections(private_app):
    client,path = private_app
    request = client.post('/api/connections', headers=signed(101), json={'peer_telegram_id':102}).json()
    cid = request['id']
    assert request['status'] == 'pending'
    assert client.get('/api/timeline/101', headers=signed(101)).json()['peers'] == []
    assert client.post(f'/api/connections/{cid}/accept', headers=signed(101)).status_code == 403
    assert client.post(f'/api/connections/{cid}/accept', headers=signed(103)).status_code == 404
    assert client.post(f'/api/connections/{cid}/accept', headers=signed(102)).status_code == 200
    assert client.get('/api/timeline/101', headers=signed(101)).json()['peers'][0]['telegram_id'] == 102
    # Even accepted peers cannot read private collections.
    assert client.get('/api/timeline/101', headers=signed(102)).status_code == 403
    assert client.delete(f'/api/connections/{cid}', headers=signed(102)).status_code == 200
    assert client.get('/api/timeline/101', headers=signed(101)).json()['peers'] == []
    assert client.post('/api/connections', headers=signed(101), json={'peer_telegram_id':101}).status_code == 400


def test_shop_owner_isolation(private_app):
    client,path = private_app
    assert client.get('/api/shops/1/stats', headers=signed(101)).status_code == 200
    assert client.get('/api/shops/1/stats', headers=signed(102)).status_code == 403
    assert client.get('/api/shops/1/orders', headers=signed(101)).status_code == 200
    assert client.get('/api/shops/1/orders', headers=signed(102)).status_code == 403
    # Owners cannot bypass payment and mint free cards.
    assert client.post('/api/shops/1/cards', headers=signed(101), json={'count':1}).status_code == 403
    assert client.get('/api/shops/mine', headers=signed(101)).json()['shops'][0]['id'] == 1
    assert client.get('/api/shops/mine', headers=signed(102)).json()['shops'] == []
    assert client.post('/api/admin/shops/1/owner', headers=signed(102), json={'telegram_id':102}).status_code == 403
    assert client.post('/api/admin/shops/1/owner', headers={'X-Admin-Token':'test-admin'}, json={'telegram_id':102}).status_code == 200
    assert client.get('/api/shops/1/stats', headers=signed(101)).status_code == 403
    assert client.get('/api/shops/1/stats', headers=signed(102)).status_code == 200


def test_account_erasure(private_app):
    client,path = private_app
    conn = connect(path)
    conn.execute("INSERT INTO gifts(id,giver_id,recipient_name,message_text) VALUES('private',2,'Name','secret')")
    conn.execute("INSERT INTO replies(gift_id,respondent_user_id,type,message_text) VALUES('private',2,'message','secret reply')")
    conn.execute("INSERT INTO notifications(telegram_id,kind,text) VALUES(102,'test','secret')")
    conn.execute("INSERT INTO payments(id,user_id,kind,amount_rub,status) VALUES('paid',2,'premium_film',499,'paid')")
    conn.execute("INSERT INTO scene_lists(user_id,name) VALUES(2,'personal')")
    conn.commit()
    assert client.post('/api/account/delete', json={'confirmation':'УДАЛИТЬ','telegram_id':102}).status_code == 401
    assert client.post('/api/account/delete', headers=signed(102), json={'confirmation':'wrong'}).status_code == 400
    assert client.post('/api/account/delete', headers=signed(102), json={'confirmation':'УДАЛИТЬ','telegram_id':103}).status_code == 200
    assert conn.execute('SELECT 1 FROM users WHERE telegram_id=102').fetchone() is None
    gift = conn.execute("SELECT * FROM gifts WHERE id='private'").fetchone()
    assert gift['status'] == 'deleted' and gift['message_text'] is None and gift['giver_id'] is None
    assert conn.execute('SELECT COUNT(*) FROM replies').fetchone()[0] == 0
    assert conn.execute("SELECT user_id FROM payments WHERE id='paid'").fetchone()[0] is None
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    # Other users survive, and no profile may be erased by a body ID alone.
    assert conn.execute('SELECT 1 FROM users WHERE telegram_id=103').fetchone()
    conn.close()


def test_erasure_blocks_owned_studio_and_pending_payment(private_app):
    client,path = private_app
    assert client.post('/api/account/delete', headers=signed(101), json={'confirmation':'УДАЛИТЬ'}).status_code == 409
    conn = connect(path)
    conn.execute("INSERT INTO payments(id,user_id,kind,amount_rub,status) VALUES('pending',2,'premium_film',499,'pending')")
    conn.commit()
    assert client.post('/api/account/delete', headers=signed(102), json={'confirmation':'УДАЛИТЬ'}).status_code == 409
    assert conn.execute('SELECT 1 FROM users WHERE id=2').fetchone()
    conn.close()
