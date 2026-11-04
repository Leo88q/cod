import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import config
from app.db import connect
from app.main import app
from app.media_access import signed_url, signature


def test_media_signed_links(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "MEDIA_SIGNING_SECRET", "test-media-secret")
    with TestClient(app) as client:
        upload = client.post("/api/upload", files={"file": ("photo.png", b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x02\x00\x00\x00\x02\x08\x02\x00\x00\x00\xfd\xd4\x9as\x00\x00\x00\x16IDATx\x9cc\xfc\xcf\xc0\xc0\xc0\xc0\xc0\xc4\xc0\xc0\xc0\xc0\xc0\x00\x00\r\x1d\x01\x03j\xc2\x9b\xe9\x00\x00\x00\x00IEND\xaeB`\x82', "image/png")})
        assert upload.status_code == 200
        path = upload.json()["path"]
        assert client.get(path).status_code == 403
        created = client.post("/api/gifts", json={"recipient_name": "Аня", "scene_ids": [12],
                                                   "message_photo": path, "consent": True})
        assert created.status_code == 200, created.text
        gid = created.json()["gift_id"]
        view = client.get(f"/api/gifts/{gid}").json()
        url = view["gift"]["message_photo_url"]
        response = client.get(url)
        assert response.status_code == 200 and response.content.startswith(b"\xff\xd8")
        assert response.headers["cache-control"] == "private, no-store"
        assert client.get(url + "x").status_code == 403
        assert client.get(url.replace("gift=" + gid, "gift=wrong")).status_code == 403
        name = path.split("/")[-1]
        expired = int(time.time()) - 1
        assert client.get(f"{path}?gift={gid}&expires={expired}&sig={signature(name, gid, expired)}").status_code == 403
        assert signed_url("/media/../secret.png", gid) is None
        conn = connect(config.DB_PATH)
        # Revocation is immediate, even with a still-valid signed URL.
        conn.execute("UPDATE gifts SET status='deleted' WHERE id=?", (gid,))
        conn.commit()
        assert client.get(url).status_code == 404
        conn.execute("UPDATE gifts SET status='created', open_at=? WHERE id=?",
                     ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), gid))
        conn.commit()
        assert client.get(url).status_code == 403
        conn.close()


def test_unattached_media_and_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEDIA_DIR", str(tmp_path))
    with TestClient(app) as client:
        response = client.post("/api/gifts", json={"recipient_name": "Боб", "scene_ids": [12]})
        gid = response.json()["gift_id"]
        (tmp_path / "unattached.png").write_bytes(b"private")
        assert client.get(signed_url("/media/unattached.png", gid)).status_code == 404
        outside = tmp_path.parent / "outside.png"
        outside.write_bytes(b"secret")
        (tmp_path / "link.png").symlink_to(outside)
        conn = connect(config.DB_PATH)
        conn.execute("UPDATE gifts SET message_photo_file_id='/media/link.png' WHERE id=?", (gid,))
        conn.commit()
        conn.close()
        assert client.get(signed_url("/media/link.png", gid)).status_code == 404
