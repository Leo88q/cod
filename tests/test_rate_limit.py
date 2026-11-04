import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

import pytest

from app.db import init_db, connect
from app.rate_limit import consume, verified_subject


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "limits.db")
    init_db(path, with_seed=False)
    return path


def test_shared_atomic_limits(db):
    # Independent connections/threads model concurrent API workers.
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: consume(db, [("ip:a", "gifts", 3)], timestamp=120), range(12)))
    assert outcomes.count(0) == 3
    assert outcomes.count(60) == 9
    assert consume(db, [("ip:a", "gifts", 3)], timestamp=180) == 0
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM rate_limit_buckets WHERE bucket < 3").fetchone()[0] == 0
    conn.close()


def test_scopes_and_rollback(db):
    assert consume(db, [("ip:a", "all", 20), ("tg:1", "gifts", 1)], timestamp=120) == 0
    assert consume(db, [("ip:b", "all", 20), ("tg:1", "gifts", 1)], timestamp=121) == 59
    # Denied user quota must not consume the other IP's allowance.
    assert consume(db, [("ip:b", "all", 1)], timestamp=121) == 0
    assert consume(db, [("ip:a", "suggest", 1)], timestamp=121) == 0


def signed_data(token, uid):
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid})}
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, "\n".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_only_verified_identities(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "BOT_TOKEN", "test-token")
    assert verified_subject(signed_data("test-token", 123)) == "tg:123"
    assert verified_subject(signed_data("wrong-token", 123)) is None
    assert verified_subject(signed_data("test-token", "123")) is None
    assert verified_subject("user=123") is None


def test_http_quota_and_retry_after(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv("RATE_LIMIT_GIFTS_PER_MIN", "1")
    with TestClient(app, client=("quota-test", 1)) as client:
        # Invalid bodies still consume anti-spam quotas.
        assert client.post("/api/gifts", json={}).status_code == 422
        denied = client.post("/api/gifts", json={})
        assert denied.status_code == 429
        assert 1 <= int(denied.headers["Retry-After"]) <= 60
        # Unrelated endpoints do not share the restrictive gifts quota.
        assert client.post("/api/scenes/search", json={}).status_code == 200
