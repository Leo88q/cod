"""Безопасность: валидация Telegram WebApp initData (HMAC по докам Telegram),
определение текущего пользователя, проверка админа."""
import hashlib
import hmac
import json
import re
import time
import urllib.parse
from typing import Optional

from fastapi import HTTPException, Request

from . import config
from .db import connect


def validate_init_data(init_data: str, bot_token: str, max_age: int = 24 * 3600) -> Optional[dict]:
    """Проверка подписи initData по документации Telegram Web Apps.
    Возвращает dict с полями initData (user — JSON-строкой) или None."""
    if not init_data or not bot_token:
        return None
    pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    parsed = dict(pairs)
    if len(parsed) != len(pairs):
        return None
    received_hash = parsed.pop("hash", None)
    if not received_hash or not re.fullmatch(r"[0-9a-f]{64}", received_hash):
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received_hash):
        return None
    try:
        auth_date = int(parsed.get("auth_date", "0"))
    except ValueError:
        return None
    if max_age and abs(time.time() - auth_date) > max_age:
        return None
    return parsed


def upsert_user_from_init_data(init_data: str) -> Optional[dict]:
    """Валидирует initData и upsert-ит пользователя. Возвращает user dict или None."""
    parsed = validate_init_data(init_data, config.BOT_TOKEN)
    if not parsed:
        return None
    try:
        tg_user = json.loads(parsed.get("user", "{}"))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(tg_user, dict):
        return None
    tg_id = tg_user.get("id")
    if type(tg_id) is not int or not 0 < tg_id < 2**63:
        return None
    fields = [tg_user.get(key) for key in ("username", "first_name", "language_code")]
    if any(value is not None and not isinstance(value, str) for value in fields):
        return None
    conn = connect(config.DB_PATH)
    try:
        conn.execute(
            "INSERT INTO users (telegram_id,username,first_name,lang) VALUES (?,?,?,?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username, "
            "first_name=excluded.first_name, lang=excluded.lang",
            (tg_id, *fields),
        )
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def request_user(request: Request, body_telegram_id: Optional[int] = None) -> Optional[dict]:
    """Текущий пользователь: сначала из заголовка X-Telegram-Init-Data,
    в dev-режиме (ALLOW_ANON) — анонимный/заявленный id из тела запроса."""
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if init_data and config.BOT_TOKEN:
        return upsert_user_from_init_data(init_data)
    if config.ALLOW_ANON and body_telegram_id:
        conn = connect(config.DB_PATH)
        try:
            conn.execute("INSERT INTO users (telegram_id) VALUES (?) ON CONFLICT(telegram_id) DO NOTHING", (body_telegram_id,))
            row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (body_telegram_id,)).fetchone()
            conn.commit()
            return dict(row)
        finally:
            conn.close()
    return None


def require_admin(request: Request) -> None:
    token = request.headers.get("X-Admin-Token", "")
    if not config.ADMIN_TOKEN or token != config.ADMIN_TOKEN:
        raise HTTPException(403, "Нужен X-Admin-Token")


def log_consent(gift_id: Optional[str], user_id: Optional[int], consent_type: str, request: Request, conn=None) -> None:
    owned = conn is None
    if owned:
        conn = connect(config.DB_PATH)
    try:
        conn.execute(
            "INSERT INTO consents (user_id, gift_id, consent_type, ip, user_agent, policy_version) VALUES (?,?,?,?,?,?)",
            (user_id, gift_id, consent_type,
             request.client.host if request.client else None,
             request.headers.get("user-agent"), config.POLICY_VERSION),
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def require_self(request: Request, telegram_id: int) -> None:
    """Private user collections are not public merely because a Telegram ID is known."""
    if config.ALLOW_ANON:
        return
    user = request_user(request)
    if not user or user["telegram_id"] != telegram_id:
        raise HTTPException(403, "Доступ только к своим данным")


def require_shop(request: Request, shop_id: int) -> None:
    """Production B2B read/write: assigned owner or global admin, never a body ID."""
    token = request.headers.get("X-Admin-Token", "")
    if config.ADMIN_TOKEN and hmac.compare_digest(token, config.ADMIN_TOKEN):
        return
    if config.ALLOW_ANON:
        return
    user = request_user(request)
    if not user:
        raise HTTPException(403, "Откройте кабинет владельца из Telegram")
    conn = connect(config.DB_PATH)
    try:
        row = conn.execute("SELECT owner_user_id FROM shops WHERE id=?", (shop_id,)).fetchone()
        if not row or row["owner_user_id"] != user["id"]:
            raise HTTPException(403, "Нет доступа к этой студии")
    finally:
        conn.close()


def require_metrics(request: Request) -> None:
    token = request.headers.get("X-Admin-Token", "")
    if config.MONITOR_TOKEN and hmac.compare_digest(token, config.MONITOR_TOKEN):
        return
    require_admin(request)
