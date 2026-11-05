"""Оплаты: ЮKassa в проде, dev-режим (кнопка-подтверждение в Mini App) локально.

Виды платежей:
- card_pack      — пачка QR-карточек студии (цена за штуку, мин. заказ)
- subscription   — месячная подписка студии (кабинет + аналитика)
- premium_film   — премиум «ваш фильм» пользователю (PDF/видео/книга, год)

Идемпотентность: эффект применяется один раз (проверка status до перехода в paid).
"""
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp

from . import config
from .db import connect, new_code


def now() -> datetime:
    return datetime.now(timezone.utc)


def amount_for(kind: str, count: int = 0) -> float:
    # The amount stored locally must be exactly the amount sent to the provider.
    try:
        if kind == "card_pack":
            raw = max(Decimal(str(config.PRICE_CARD_PACK_MIN)),
                      Decimal(count) * Decimal(str(config.PRICE_PER_CARD)))
        elif kind == "subscription":
            raw = Decimal(str(config.PRICE_SUBSCRIPTION_MONTH))
        elif kind == "premium_film":
            raw = Decimal(str(config.PRICE_PREMIUM_FILM))
        else:
            raise ValueError(f"unknown kind: {kind}")
        if not raw.is_finite():
            raise ValueError("Invalid payment amount")
        amount = raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if amount <= 0:
            raise ValueError("Invalid payment amount")
        return float(amount)
    except InvalidOperation as exc:
        raise ValueError("Invalid payment amount") from exc


async def _create_provider_payment(pid: str) -> dict:
    """Retry with the persisted idempotence key; concurrent attempts are safe."""
    conn = connect(config.DB_PATH)
    try:
        row = conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
        if not row:
            raise ValueError("Unknown local payment")
        p = dict(row)
    finally:
        conn.close()
    if p["status"] != "creating":
        return p
    payload = json.loads(p["payload"])
    body = {
        "amount": {"value": f"{p['amount_rub']:.2f}", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": payload["return_url"]},
        "capture": True,
        "description": f"Сцена: {p['kind']}" + (f" ({payload['count']} шт.)" if payload.get("count") else ""),
        "metadata": {"payment_id": pid},
    }
    auth = aiohttp.BasicAuth(config.YUKASSA_SHOP_ID, config.YUKASSA_SECRET_KEY)
    async with aiohttp.ClientSession(auth=auth, timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.post(f"{config.YUKASSA_API}/payments", json=body,
                                headers={"Idempotence-Key": pid}) as response:
            response.raise_for_status()
            data = await response.json()
    provider_id = data.get("id")
    url = (data.get("confirmation") or {}).get("confirmation_url")
    if not provider_id or (not url and data.get("status") not in ("succeeded", "canceled")):
        raise ValueError("Invalid payment provider response")
    terminal = data.get("status") if data.get("status") in ("succeeded", "canceled") else None
    next_status = "verifying_" + terminal if terminal else "pending"
    conn = connect(config.DB_PATH)
    try:
        conn.execute("UPDATE payments SET provider_payment_id=?, confirmation_url=?, status=? "
                     "WHERE id=? AND status='creating'", (provider_id, url, next_status, pid))
        conn.commit()
        p = dict(conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone())
    finally:
        conn.close()
    if p["status"].startswith("verifying_"):
        await _reconcile_verifying_payment(p)
        p = get_payment(pid)
    return p


async def _reconcile_verifying_payment(p: dict) -> None:
    status = p["status"].removeprefix("verifying_")
    if status not in ("succeeded", "canceled"):
        raise ValueError("Invalid reconciliation status")
    event = {"event": "payment." + status, "object": {"id": p["provider_payment_id"]}}
    verified = await verify_provider_event(event)
    import asyncio
    await asyncio.to_thread(handle_provider_webhook, verified)


async def recover_creating_payments() -> None:
    """Recover uncertain provider calls within the provider idempotency window.

    Older records need operator reconciliation, never a fresh charge after the
    idempotence key may have expired.
    """
    import logging
    conn = connect(config.DB_PATH)
    try:
        rows = conn.execute("SELECT * FROM payments WHERE provider='yookassa' AND "
                            "((status='creating' AND datetime(created_at)>datetime('now','-23 hours')) "
                            "OR status IN ('verifying_succeeded','verifying_canceled')) "
                            "ORDER BY created_at LIMIT 10").fetchall()
    finally:
        conn.close()
    for row in rows:
        try:
            if row["status"] == "creating":
                await _create_provider_payment(row["id"])
            else:
                await _reconcile_verifying_payment(dict(row))
        except Exception:
            logging.getLogger("scena.payments").exception("Payment creation recovery failed id=%s", row["id"])


async def create_payment(kind: str, *, user_id: Optional[int] = None, shop_id: Optional[int] = None,
                         count: int = 0, base_url: str = "") -> dict:
    if kind == "card_pack" and not 1 <= count <= 10000:
        raise ValueError("Invalid card count")
    amount = amount_for(kind, count)
    pid = new_code(12)
    provider = "yookassa" if (config.YUKASSA_SHOP_ID and config.YUKASSA_SECRET_KEY) else "dev"
    return_url = f"{base_url}/app/#/pay/{pid}"
    payload = json.dumps({"kind": kind, "shop_id": shop_id, "count": count, "return_url": return_url}, ensure_ascii=False)
    status = "creating" if provider == "yookassa" else "pending"
    conn = connect(config.DB_PATH)
    try:
        conn.execute(
            "INSERT INTO payments (id,user_id,shop_id,kind,payload,amount_rub,status,provider,confirmation_url) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, user_id, shop_id, kind, payload, amount, status, provider,
             None if provider == "yookassa" else return_url))
        conn.commit()
    finally:
        conn.close()
    if provider == "yookassa":
        try:
            p = await _create_provider_payment(pid)
        except Exception:
            # The remote request may have succeeded. Do not create a second
            # payment: the worker will retry the persisted idempotence key.
            import logging
            logging.getLogger("scena.payments").exception("Payment creation deferred id=%s", pid)
            p = get_payment(pid)
    else:
        p = get_payment(pid)
    return {"payment_id": pid, "kind": kind, "amount_rub": amount, "status": p["status"],
            "provider": provider, "confirmation_url": p["confirmation_url"]}


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
    if not current or current["status"] not in ("pending", "verifying_succeeded", "verifying_canceled"):
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
        row = conn.execute("SELECT subscribed_until FROM shops WHERE id=?", (shop_id,)).fetchone()
        previous = datetime.fromisoformat(row[0]) if row and row[0] else now()
        if previous.tzinfo is None: previous = previous.replace(tzinfo=timezone.utc)
        until = (max(now(), previous) + timedelta(days=30)).isoformat()
        conn.execute("UPDATE shops SET subscription='active', subscribed_until=? WHERE id=?",
                     (until, shop_id))
    elif kind == "premium_film":
        if p["user_id"]:
            row = conn.execute("SELECT premium_until FROM users WHERE id=?", (p["user_id"],)).fetchone()
            previous = datetime.fromisoformat(row[0]) if row and row[0] else now()
            if previous.tzinfo is None: previous = previous.replace(tzinfo=timezone.utc)
            until = (max(now(), previous) + timedelta(days=365)).isoformat()
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
    if p["provider"] != "dev":
        conn.close()
        return None
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
        conn.execute("UPDATE payments SET status='cancelled' WHERE id=? AND status IN ('pending','verifying_succeeded','verifying_canceled')", (p["id"],))
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
        # Webhook may arrive before the local provider ID is persisted.
        raise HTTPException(503, "Payment reference not yet available; retry")
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
