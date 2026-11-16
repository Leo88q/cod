"""Переход /go/{code}: без верной подписи — отказ, а не редирект."""
import pytest
from fastapi.testclient import TestClient

from app import config
from app import marketplaces as mk


@pytest.fixture()
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture()
def secret(monkeypatch):
    monkeypatch.setattr(config, "PARTNER_SECRET", "test-partner-secret")
    return "test-partner-secret"


def _path(code, target, secret):
    return mk.offer_path("ozon", code, target, secret)


def test_valid_signature_redirects(client, secret):
    target = "https://ozon.ru/product/123"
    path = _path("gift1", target, secret)
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == target


def test_missing_signature_forbidden(client, secret):
    r = client.get("/go/gift1?p=ozon&u=https://ozon.ru/product/123", follow_redirects=False)
    assert r.status_code == 403


def test_tampered_target_forbidden(client, secret):
    path = _path("gift1", "https://ozon.ru/product/123", secret)
    forged = path.replace("product%2F123", "product%2F999")
    r = client.get(forged, follow_redirects=False)
    assert r.status_code == 403


def test_foreign_host_forbidden(client, secret):
    # подпись посчитана честно, но хост чужой — проверка хоста независима от подписи
    import hmac, hashlib
    target = "https://evil.example/steal"
    base = f"ozon|gift1|{target}"
    sig = hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()[:32]
    from urllib.parse import quote
    r = client.get(f"/go/gift1?p=ozon&sig={sig}&u={quote(target, safe='')}",
                   follow_redirects=False)
    assert r.status_code == 403


def test_disabled_without_secret(client, monkeypatch):
    monkeypatch.setattr(config, "PARTNER_SECRET", "")
    r = client.get("/go/gift1?p=ozon&sig=abc&u=https://ozon.ru/x", follow_redirects=False)
    assert r.status_code == 403


def test_offer_endpoint_requires_secret(client, monkeypatch):
    monkeypatch.setattr(config, "PARTNER_SECRET", "")
    r = client.post("/api/gifts/any/offer", json={"partner": "ozon", "url": "https://ozon.ru/x"})
    assert r.status_code == 503
