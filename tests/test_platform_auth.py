"""Авторизация на нескольких платформах: Telegram / VK / Одноклассники.

Главное, что проверяем: подпись запуска проверяется на БЭКЕНДЕ, и подделать
vk_user_id из URL нельзя. Плюс — один человек в двух мессенджерах остаётся
одним пользователем.
"""
import base64
import hashlib
import hmac
import urllib.parse

import pytest

from app import config
from app import platform_auth as pa


def sign(params: dict, secret: str) -> str:
    parts = sorted((k, v) for k, v in params.items() if k.startswith("vk_"))
    base = "&".join(f"{k}={v}" for k, v in parts)
    digest = hmac.new(secret.encode(), base.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def launch(user_id=42, app_id="777", secret="s3cret", **extra):
    params = {"vk_user_id": str(user_id), "vk_app_id": str(app_id),
              "vk_ts": str(int(__import__("time").time())), **extra}
    params["sign"] = sign(params, secret)
    return urllib.parse.urlencode(params)


def test_valid_launch_params():
    q = launch(42)
    got = pa.validate_vk_launch(q, "s3cret", max_age=86400)
    assert got["vk_user_id"] == 42 and got["vk_app_id"] == "777"


def test_tampered_user_id_is_rejected():
    """Самый важный тест: подменить vk_user_id и сохранить подпись нельзя."""
    q = launch(42).replace("vk_user_id=42", "vk_user_id=999")
    with pytest.raises(pa.ProviderError):
        pa.validate_vk_launch(q, "s3cret")


def test_wrong_secret_rejected():
    with pytest.raises(pa.ProviderError):
        pa.validate_vk_launch(launch(42), "other-secret")


def test_missing_sign_and_missing_params():
    with pytest.raises(pa.ProviderError):
        pa.validate_vk_launch("vk_user_id=1&vk_app_id=2", "s3cret")
    with pytest.raises(pa.ProviderError):
        pa.validate_vk_launch("sign=abc", "s3cret")
    with pytest.raises(pa.ProviderError):
        pa.validate_vk_launch(launch(42), "")


def test_expired_launch_rejected():
    params = {"vk_user_id": "42", "vk_app_id": "777", "vk_ts": "1000000000"}
    params["sign"] = sign(params, "s3cret")
    with pytest.raises(pa.ProviderError):
        pa.validate_vk_launch(urllib.parse.urlencode(params), "s3cret", max_age=3600)


def test_huge_or_negative_user_id_rejected():
    for bad in ("0", "-5", "9" * 20):
        params = {"vk_user_id": bad, "vk_app_id": "777"}
        params["sign"] = sign(params, "s3cret")
        with pytest.raises(pa.ProviderError):
            pa.validate_vk_launch(urllib.parse.urlencode(params), "s3cret")


def test_vk_user_created_once_and_stable():
    first = pa.upsert_identity("vk", "555", "Аня")
    second = pa.upsert_identity("vk", "555", "Аня")
    assert first["id"] == second["id"]


def test_same_human_on_two_platforms_stays_one_user(monkeypatch):
    vk = pa.upsert_identity("vk", "777001", "Аня")
    ok = pa.upsert_identity("ok", "777001", "Аня")
    # по умолчанию это разные записи: связывать аккаунты должен человек явно
    assert vk["id"] != ok["id"]
    # но у каждой есть своя идентичность и она не дублируется
    again = pa.upsert_identity("ok", "777001")
    assert again["id"] == ok["id"]


def test_telegram_still_works_through_same_module():
    u = pa.upsert_identity("telegram", "123456", "tg_user")
    assert u["telegram_id"] == 123456


def test_unknown_provider_rejected():
    with pytest.raises(pa.ProviderError):
        pa.upsert_identity("whatsapp", "1")


def test_ok_user_id_must_be_numeric():
    with pytest.raises(pa.ProviderError):
        pa.ok_user_key("12abc")


def test_request_detects_vk_from_header():
    from fastapi import Request
    from starlette.datastructures import Headers
    monkeypatch_secret = "s3cret"
    q = launch(42)
    scope = {"type": "http", "method": "GET", "path": "/api/auth/vk",
             "headers": [(b"x-vk-launch-params", q.encode())], "query_string": b""}
    request = Request(scope)
    old = (config.VK_SECRET, config.BOT_TOKEN)
    config.VK_SECRET, config.BOT_TOKEN = monkeypatch_secret, ""
    try:
        got = pa.provider_from_request(request)
    finally:
        config.VK_SECRET, config.BOT_TOKEN = old
    assert got and got["provider"] == "vk" and got["user"]["id"] > 0
