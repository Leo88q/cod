from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import config, operations, scheduler
from app.db import connect, init_db
from app.main import app
from scripts.monitor import evaluate

ADMIN = {'X-Admin-Token':'test-admin'}


@pytest.fixture
def operational_db(tmp_path, monkeypatch):
    path = str(tmp_path / 'operations.db')
    media = tmp_path / 'media'
    media.mkdir()
    init_db(path, with_seed=False)
    monkeypatch.setattr(config,'DB_PATH',path)
    monkeypatch.setattr(config,'MEDIA_DIR',str(media))
    conn=connect(path)
    conn.execute("INSERT INTO notifications(id,telegram_id,kind,text,url,sent,attempts) VALUES(1,123,'reply','secret text','/g/secret',-1,8)")
    conn.commit()
    conn.close()
    return path


def test_failed_queue_and_atomic_retry(operational_db, monkeypatch):
    client=TestClient(app)
    assert client.get('/api/admin/notifications/failed').status_code == 403
    response=client.get('/api/admin/notifications/failed',headers=ADMIN)
    assert response.status_code == 200
    assert 'secret' not in response.text and 'telegram_id' not in response.text
    assert client.post('/api/admin/notifications/1/retry').status_code == 403
    assert client.post('/api/admin/notifications/1/retry',headers=ADMIN).status_code == 200
    assert client.post('/api/admin/notifications/1/retry',headers=ADMIN).status_code == 409
    entries=client.get('/api/admin/audit',headers=ADMIN).json()['entries']
    assert len(entries)==1 and entries[0]['action']=='notification_retry'
    assert entries[0]['actor']=='admin-token'
    assert client.get('/api/admin/audit?limit=201',headers=ADMIN).status_code == 422
    # The state change is rolled back if its audit insert fails.
    conn=connect(operational_db)
    conn.execute('UPDATE notifications SET sent=-1,attempts=8 WHERE id=1')
    conn.commit()
    def broken_audit(*args, **kwargs): raise RuntimeError('failure')
    monkeypatch.setattr(operations,'audit',broken_audit)
    assert client.post('/api/admin/notifications/1/retry',headers=ADMIN).status_code == 500
    assert conn.execute('SELECT sent FROM notifications WHERE id=1').fetchone()[0] == -1
    conn.close()


def test_worker_heartbeat_failure_and_retention(operational_db, monkeypatch):
    conn=connect(operational_db)
    assert operations.worker_health(conn)['status']=='missing'
    conn.execute("INSERT INTO audit_log(action,actor,resource,created_at) VALUES('old','admin-token','1','2000-01-01')")
    conn.commit()
    def fail_task(conn): raise ValueError('test task failure')
    monkeypatch.setattr(scheduler,'_gift_soon',fail_task)
    assert scheduler.tick(False)==1
    health=operations.worker_health(conn)
    assert health['status']=='degraded' and health['delivery_enabled'] is False
    assert conn.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0]==0
    conn.execute("UPDATE worker_state SET heartbeat_at=?", ((scheduler.now()-timedelta(days=2)).isoformat(),))
    conn.commit()
    assert operations.worker_health(conn)['status']=='stale'
    conn.close()


def test_owner_transfer_is_audited(operational_db):
    conn=connect(operational_db)
    conn.execute('INSERT INTO users(id,telegram_id) VALUES(1,12345)')
    conn.execute("INSERT INTO shops(id,name) VALUES(1,'test')")
    conn.commit()
    client=TestClient(app)
    assert client.post('/api/admin/shops/1/owner',headers=ADMIN,json={'telegram_id':12345}).status_code==200
    row=conn.execute('SELECT * FROM audit_log').fetchone()
    assert row['action']=='shop_owner_assigned' and row['resource']=='1'
    assert '12345' not in row['details']
    conn.close()


def test_monitor_decisions():
    metrics={'database':'ok','worker':{'status':'ok','delivery_enabled':True},
             'pending_notifications':0,'failed_notifications':0,'requests_this_process':{'5xx':3}}
    assert evaluate(metrics,3)==[]
    assert 'new_5xx' in evaluate(metrics,2)
    metrics['worker']['status']='stale'
    metrics['worker']['delivery_enabled']=False
    metrics['pending_notifications']=500
    metrics['failed_notifications']=1
    reasons=evaluate(metrics,3)
    assert {'worker_unhealthy','notification_backlog','dead_letter_notifications',
            'delivery_disabled_with_pending_notifications'} <= set(reasons)


def test_monitor_credential_is_read_only(operational_db,monkeypatch):
    monkeypatch.setattr(config,'MONITOR_TOKEN','read-only-test-token')
    client=TestClient(app)
    headers={'X-Admin-Token':'read-only-test-token'}
    assert client.get('/api/admin/metrics',headers=headers).status_code==200
    assert client.get('/api/admin/audit',headers=headers).status_code==403
    assert client.get('/api/admin/notifications/failed',headers=headers).status_code==403
    assert client.post('/api/admin/notifications/1/retry',headers=headers).status_code==403
