"""API «вашего кино»: фото+голос -> MP4 без кадров из фильмов."""
import io
import os
import subprocess
import sys

import imageio_ffmpeg
from fastapi.testclient import TestClient
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _jpg(color=(40, 60, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1200, 900), color).save(buf, "JPEG", quality=88)
    return buf.getvalue()


def _mp3(seconds=3.0) -> bytes:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    res = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                          "-i", f"sine=frequency=220:duration={seconds}",
                          "-af", "volume=0.2", "-f", "mp3", "pipe:1"], capture_output=True)
    return res.stdout


def _headers():
    # dev-режим тестов (ALLOW_ANON=1), но эндпоинт требует признак авторизации
    return {"X-Telegram-Init-Data": "dev"}


def _upload(client, data: bytes, name: str) -> str:
    r = client.post("/api/upload", files={"file": (name, data, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()["path"]


def test_build_film_from_uploaded_photos():
    from app.main import app
    client = TestClient(app)
    photos = [_upload(client, _jpg((30 + i * 30, 50, 80)), f"p{i}.jpg") for i in range(3)]
    voice = _upload(client, _mp3(4.0), "voice.mp3")
    r = client.post("/api/film/build", headers=_headers(), json={
        "photos": photos,
        "captions": ["как мы познакомились", "первое свидание", "и вот мы здесь"],
        "title": "Ане",
        "voice": voice,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["frames"] == 3 and body["bytes"] > 5000
    assert body["url"] and body["url"].startswith("/media/film_")
    assert body["url"].find("sig=") > 0, "видео отдаётся только по подписанной ссылке"


def test_rejects_foreign_paths():
    """Через ../ нельзя прочитать произвольный файл сервера."""
    from app.main import app
    client = TestClient(app)
    for bad in ["/etc/passwd", "../../app/config.py", "/media/../app/config.py", "media/x.jpg"]:
        r = client.post("/api/film/build", headers=_headers(),
                        json={"photos": [bad], "captions": [], "title": ""})
        assert r.status_code in (400, 404), f"{bad} -> {r.status_code}"


def test_requires_auth():
    from app.main import app
    client = TestClient(app)
    photo = _upload(client, _jpg(), "n.jpg")
    r = client.post("/api/film/build", json={"photos": [photo]})
    assert r.status_code == 401


def test_missing_media_returns_404():
    from app.main import app
    client = TestClient(app)
    r = client.post("/api/film/build", headers=_headers(),
                    json={"photos": ["/media/nonexistent123.jpg"]})
    assert r.status_code == 404


def test_photo_count_limit():
    from app.main import app
    client = TestClient(app)
    photo = _upload(client, _jpg(), "lim.jpg")
    r = client.post("/api/film/build", headers=_headers(), json={"photos": [photo] * 11})
    assert r.status_code == 422
