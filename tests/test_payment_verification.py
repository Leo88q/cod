import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from app import config, payments
from app.db import connect, init_db


@pytest.fixture
def payment_db(tmp_path, monkeypatch):
    path = str(tmp_path / "payments.db")
    init_db(path, with_seed=False)
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "YUKASSA_SHOP_ID", "12345")
    monkeypatch.setattr(config, "YUKASSA_SECRET_KEY", "test-secret")
    conn = connect(path)
    conn.execute("INSERT INTO users (id,telegram_id) VALUES(1,123)")
    conn.execute("INSERT INTO payments (id,user_id,kind,payload,amount_rub,status,provider,provider_payment_id) "
                 "VALUES('local',1,'premium_film','{}',499,'pending','yookassa','provider-1')")
    conn.commit()
    conn.close()
    return path


def test_payment_effect_atomic(payment_db):
    def apply(_):
        conn = connect(payment_db)
        try:
            return payments.apply_payment(conn, {"id": "local"})
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(apply, range(8)))
    assert results.count(True) == 1


def test_verify_provider_status_and_amount(payment_db, monkeypatch):
    verified = {"id": "provider-1", "status": "succeeded", "paid": True,
                "amount": {"value": "499.00", "currency": "RUB"},
                "metadata": {"payment_id": "local"}, "recipient": {"account_id": "12345"}}

    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def json(self): return verified

    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def get(self, url):
            assert url.endswith("/payments/provider-1")
            return Response()

    monkeypatch.setattr(payments.aiohttp, "ClientSession", Session)
    event = {"event": "payment.succeeded", "object": {"id": "provider-1", "amount": {"value": "0"}}}
    assert asyncio.run(payments.verify_provider_event(event))["object"]["metadata"]["payment_id"] == "local"
    verified["amount"]["value"] = "1.00"
    with pytest.raises(HTTPException) as error:
        asyncio.run(payments.verify_provider_event(event))
    assert error.value.status_code == 403
    verified["amount"]["value"] = "499.00"
    verified["status"] = "pending"
    with pytest.raises(HTTPException):
        asyncio.run(payments.verify_provider_event(event))
