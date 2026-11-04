"""Оплаты: ЮKassa в проде, dev-режим (кнопка-подтверждение в Mini App) локально.

Виды платежей:
- card_pack      — пачка QR-карточек студии (цена за штуку, мин. заказ)
- subscription   — месячная подписка студии (кабинет + аналитика)
- premium_film   — премиум «ваш фильм» пользователю (PDF/видео/книга, год)

Идемпотентность: эффект применяется один раз (проверка status до перехода в paid).
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp

from . import config
from .db import connect, new_code


def now() -> datetime:
    return datetime.now(timezone.utc)


def amount_for(kind: str, count: int = 0) -> float:
    if kind == "card_pack":
        return max(config.PRICE_CARD_PACK_MIN, count * config.PRICE_PER_CARD)
    if kind == "subscription":
        return config.PRICE_SUBSCRIPTION_MONTH
    if kind == "premium_film":
        return config.PRICE_PREMIUM_FILM
    raise ValueError(f"unknown kind: {kind}")


async def create_payment(kind: str, *, user_id: Optional[int] = None, shop_id: Optional[int] = None,
                         count: int = 0, base_url: str = "") -> dict:
    amount = amount_for(kind, count)
    pid = new_code(12)
    payload = json.dumps({"kind": kind, "shop_id": shop_id, "count": count}, ensure_ascii=False)
    provider = "yookassa" if (config.YUKASSA_SHOP_ID and config.YUKASSA_SECRET_KEY) else "dev"
    confirmation_url = f"{base_url}/app/#/pay/{pid}"
    provider_payment_id = None

    if provider == "yookassa":
        # реальное создание платежа в ЮKassa (подтверждение typu redirect)
        body = {
            "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": f"{base_url}/app/#/pay/{pid}"},
            "capture": True,
            "description": f"Сцена: {kind}" + (f" ({count} шт.)" if count else ""),
            "metadata": {"payment_id": pid},
        }
        auth = aiohttp.BasicAuth(config.YUKASSA_SHOP_ID, config.YUKASSA_SECRET_KEY)
        async with aiohttp.ClientSession(auth=auth, timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(f"{config.YUKASSA_API}/payments",
                              json=body, headers={"Idempotence-Key": pid}) as r:
                r.raise_for_status()
                data = await r.json()
                if not data.get("id") or not (data.get("confirmation") or {}).get("confirmation_url"):
                    raise ValueError("Invalid payment provider response")
                provider_payment_id = data.get("id")
                confirmation_url = (data.get("confirmation") or {}).get("confirmation_url", confirmation_url)

    conn = connect(config.DB_PATH)
    conn.execute(
        "INSERT INTO payments (id, user_id, shop_id, kind, payload, amount_rub, status, provider,"
        " provider_payment_id, confirmation_url) VALUES (?,?,?,?,?,?,'pending',?,?,?)",
        (pid, user_id, shop_id, kind, payload, amount, provider, provider_payment_id, confirmation_url),
    )
    conn.commit()
    conn.close()
    return {"payment_id": pid, "kind": kind, "amount_rub": amount, "status": "pending",
            "provider": provider, "confirmation_url": confirmation_url}


def get_payment(pid: str) -> Optional[dict]:
    conn = connect(config.DB_PATH)
    row = conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def apply_payment(conn, p: dict) -> bool:
    """Применяет эффект платежа идемпотентно. True — если применили сейчас."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    current = conn.execute("SELECT * FROM payments WHERE id=?", (p["id"],)).fetchone()
    if not current or current["status"] != "pending":
        conn.commit()
        return False
    p = dict(current)
    payload = json.loads(p["payload"] or "{}")
    kind = p["kind"]
    if kind == "card_pack":
        shop_id = p["shop_id"] or payload.get("shop_id")
        count = int(payload.get("count") or 0)
        for _ in range(count):
            conn.execute("INSERT INTO cards (code, shop_id, printed_at) VALUES (?,?,?)",
                         (new_code(8), shop_id, now().isoformat()))
        conn.execute("UPDATE shops SET card_balance = card_balance + ? WHERE id=?", (count, shop_id))
    elif kind == "subscription":
        shop_id = p["shop_id"] or payload.get("shop_id")
        until = (now() + timedelta(days=30)).isoformat()
        conn.execute("UPDATE shops SET subscription='active', subscribed_until=? WHERE id=?",
                     (until, shop_id))
    elif kind == "premium_film":
        if p["user_id"]:
            until = (now() + timedelta(days=365)).isoformat()
            conn.execute("UPDATE users SET premium_until=? WHERE id=?", (until, p["user_id"]))
    conn.execute("UPDATE payments SET status='paid', paid_at=? WHERE id=? AND status<>'paid'",
                 (now().isoformat(), p["id"]))
    conn.commit()
    return True


def confirm_dev(pid: str) -> Optional[dict]:
    """Dev-подтверждение оплаты (когда ЮKassa не настроена)."""
    conn = connect(config.DB_PATH)
    row = conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close()
        return None
    p = dict(row)
    applied = apply_payment(conn, p)
    row = conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    conn.close()
    return {**dict(row), "applied_now": applied}


def handle_provider_webhook(event: dict) -> bool:
    """Webhook ЮKassa: {"event": "payment.succeeded", "object": {"id": ..., "metadata": {"payment_id": ...}}}"""
    obj = event.get("object") or {}
    pid = (obj.get("metadata") or {}).get("payment_id") or obj.get("id")
    if event.get("event") not in ("payment.succeeded", "payment.canceled"):
        return False
    conn = connect(config.DB_PATH)
    row = conn.execute("SELECT * FROM payments WHERE id=? OR provider_payment_id=?", (pid, pid)).fetchone()
    if not row:
        conn.close()
        return False
    p = dict(row)
    if event["event"] == "payment.succeeded":
        apply_payment(conn, p)
    else:
        conn.execute("UPDATE payments SET status='cancelled' WHERE id=? AND status='pending'", (p["id"],))
        conn.commit()
    conn.close()
    return True


async def verify_provider_event(event: dict) -> dict:
    """Do not trust incoming status/amount/metadata: read payment from provider API.

    Incoming webhook Basic auth is not assumed. Our outgoing API request uses
    merchant credentials. Fail closed on mismatch; API outages must be retried.
    """
    from decimal import Decimal, InvalidOperation
    from fastapi import HTTPException
    import re
    kind = event.get("event")
    if kind not in ("payment.succeeded", "payment.canceled"):
        return {"event": "ignored"}
    obj = event.get("object") or {}
    provider_id = obj.get("id", "") if isinstance(obj, dict) else ""
    if not isinstance(provider_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,100}", provider_id):
        raise HTTPException(403, "Invalid payment reference")
    conn = connect(config.DB_PATH)
    try:
        row = conn.execute("SELECT * FROM payments WHERE provider_payment_id=? AND provider='yookassa'",
                           (provider_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(403, "Unknown payment")
    p = dict(row)
    auth = aiohttp.BasicAuth(config.YUKASSA_SHOP_ID, config.YUKASSA_SECRET_KEY)
    try:
        async with aiohttp.ClientSession(auth=auth, timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(f"{config.YUKASSA_API}/payments/{provider_id}") as response:
                response.raise_for_status()
                verified = await response.json()
    except (aiohttp.ClientError, TimeoutError, ValueError):
        raise HTTPException(503, "Payment verification temporarily unavailable")
    try:
        valid = (verified["id"] == provider_id
                 and verified["amount"]["currency"] == "RUB"
                 and Decimal(verified["amount"]["value"]) == Decimal(str(p["amount_rub"]))
                 and verified.get("metadata", {}).get("payment_id") == p["id"]
                 and str(verified.get("recipient", {}).get("account_id")) == str(config.YUKASSA_SHOP_ID))
    except (KeyError, TypeError, InvalidOperation):
        valid = False
    if not valid:
        raise HTTPException(403, "Payment verification mismatch")
    expected = "succeeded" if kind == "payment.succeeded" else "canceled"
    if verified.get("status") != expected or (expected == "succeeded" and verified.get("paid") is not True):
        raise HTTPException(403, "Payment status mismatch")
    return {"event": kind, "object": {"id": provider_id, "metadata": {"payment_id": p["id"]}}}
