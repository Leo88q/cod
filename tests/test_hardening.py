import gzip
import sqlite3
from datetime import timedelta

from fastapi.testclient import TestClient

from app import config, scheduler
from app.db import connect, init_db
from app.main import app


def test_private_collections_require_identity(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_ANON", False)
    with TestClient(app) as client:
        for path in ("/api/timeline/123", "/api/film/123", "/api/film/123/export", "/api/dates/123/ics"):
            assert client.get(path).status_code == 403


def test_diagnostics_and_readiness():
    with TestClient(app) as client:
        response = client.get("/ready?secret=do-not-log")
        assert response.status_code == 200
        assert len(response.headers["X-Request-ID"]) == 32
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert client.get("/api/admin/metrics").status_code == 403
        metrics = client.get("/api/admin/metrics", headers={"X-Admin-Token": "test-admin"})
        assert metrics.json()["database"] == "ok"


def test_reminder_transaction_and_time_window(tmp_path, monkeypatch):
    path = str(tmp_path / "scheduler.db")
    init_db(path, with_seed=False)
    monkeypatch.setattr(config, "DB_PATH", path)
    conn = connect(path)
    conn.execute("INSERT INTO users (id,telegram_id) VALUES(1,123)")
    for gid, delta in (("past", -1), ("soon", 20), ("far", 30)):
        conn.execute("INSERT INTO gifts (id,giver_id,recipient_name,open_at) VALUES(?,1,'Аня',?)",
                     (gid, (scheduler.now() + timedelta(hours=delta)).isoformat()))
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    scheduler._gift_soon(conn)
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0
    scheduler._gift_soon(conn)
    conn.commit()
    scheduler._gift_soon(conn)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
    assert conn.execute("SELECT soon_notified FROM gifts WHERE id='past'").fetchone()[0] == 0
    conn.close()


def test_cleanup_orphan_and_reply_media(tmp_path, monkeypatch):
    import os
    path = str(tmp_path / "cleanup.db")
    media = tmp_path / "media"
    media.mkdir()
    init_db(path, with_seed=False)
    monkeypatch.setattr(config, "MEDIA_DIR", str(media))
    conn = connect(path)
    old = (scheduler.now() - timedelta(days=9)).isoformat()
    conn.execute("INSERT INTO gifts (id,recipient_name,status,inactive_at) VALUES('dead','x','deleted',?)", (old,))
    conn.execute("INSERT INTO replies (gift_id,type,media_path) VALUES('dead','message','/media/reply.png')")
    for name in ("reply.png", "orphan.png", "fresh.png"):
        (media / name).write_bytes(b"test")
    os.utime(media / "orphan.png", (0, 0))
    conn.commit()
    scheduler._cleanup_media(conn)
    conn.commit()
    assert not (media / "reply.png").exists()
    assert not (media / "orphan.png").exists()
    assert (media / "fresh.png").exists()
    conn.close()


def test_backup_restore_check(tmp_path):
    from scripts.verify_backup import verify
    path = str(tmp_path / "source.db")
    init_db(path, with_seed=False)
    conn = sqlite3.connect(path)
    dest = str(tmp_path / "copy.db")
    copy = sqlite3.connect(dest)
    conn.backup(copy)
    copy.close()
    conn.close()
    archive = tmp_path / "copy.db.gz"
    with open(dest, "rb") as source, gzip.open(archive, "wb") as target:
        target.write(source.read())
    verify(archive)


def test_deleted_gift_cannot_be_resurrected():
    with TestClient(app) as client:
        response = client.post("/api/gifts", json={"giver_telegram_id": 10101, "recipient_name": "Аня", "scene_ids": [12]})
        gid = response.json()["gift_id"]
        assert client.post(f"/api/gifts/{gid}/delete?telegram_id=10101").status_code == 200
        assert client.post(f"/api/gifts/{gid}/reply", json={"type": "reaction", "reaction": "❤️"}).status_code == 404
        assert client.post(f"/api/gifts/{gid}/notes", json={"text": "note"}).status_code == 404


def test_preflight_has_no_false_production_success():
    from scripts.preflight import check
    assert check({})
    env = {"ALLOW_ANON": "0", "ADMIN_TOKEN": "a" * 40, "MEDIA_SIGNING_SECRET": "b" * 40,
           "WEBHOOK_SECRET": "c" * 40, "BOT_TOKEN": "test-token", "PUBLIC_URL": "https://example.org",
           "SUPPORT_CONTACT": "support@example.org", "OPERATOR_NAME":"Test operator",
           "OPERATOR_INN":"test", "OPERATOR_CONTACT":"privacy@example.org", "HOSTING_REGION":"RU"}
    assert check(env) == []
    env["YUKASSA_SHOP_ID"] = "123"
    assert check(env)


def test_notification_backoff(tmp_path, monkeypatch):
    import asyncio
    path = str(tmp_path / "notifications.db")
    init_db(path, with_seed=False)
    monkeypatch.setattr(config, "DB_PATH", path)
    conn = connect(path)
    conn.execute("INSERT INTO notifications (telegram_id,kind,text) VALUES(1,'test','hello')")
    conn.commit()

    class BrokenBot:
        async def send_message(self, *args, **kwargs):
            raise RuntimeError("secret must not be logged")

    asyncio.run(scheduler._send_notifications(BrokenBot()))
    row = conn.execute("SELECT * FROM notifications").fetchone()
    assert row["attempts"] == 1 and row["sent"] == 0 and row["next_attempt_at"]
    asyncio.run(scheduler._send_notifications(BrokenBot()))
    assert conn.execute("SELECT attempts FROM notifications").fetchone()[0] == 1
    conn.close()
