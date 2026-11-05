"""Фоновые задачи: отложенные открытия и «сгорание» подарков, напоминания о датах,
рассылка уведомлений из очереди через бота."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from . import config
from .db import connect, enqueue_notification

log = logging.getLogger("scena.scheduler")


def now() -> datetime:
    return datetime.now(timezone.utc)


def _expire_and_ttl(conn) -> None:
    ttl = timedelta(days=config.GIFT_TTL_DAYS)
    now_iso = now().isoformat()
    conn.execute(
        "UPDATE gifts SET status='expired', inactive_at=COALESCE(inactive_at, CURRENT_TIMESTAMP) WHERE status='created' AND open_at IS NOT NULL"
        " AND datetime(open_at) < datetime(?, ?)",
        (now_iso, f"-{config.GIFT_TTL_DAYS} days"),
    )
    conn.execute(
        "UPDATE gifts SET status='expired', inactive_at=COALESCE(inactive_at, CURRENT_TIMESTAMP) WHERE status='created' AND open_at IS NULL"
        " AND datetime(created_at) < datetime(?, ?)",
        (now_iso, f"-{config.GIFT_TTL_DAYS} days"),
    )


def _remind_dates(conn) -> None:
    today = now().date()
    year = today.year
    rows = conn.execute("SELECT * FROM important_dates").fetchall()
    for r in rows:
        try:
            mm, dd = map(int, r["date"].split("-"))
            # Find the next occurrence, including leap-day anniversaries.
            d = None
            for occurrence_year in range(year, year + 9):
                try:
                    candidate = datetime(occurrence_year, mm, dd).date()
                except ValueError:
                    continue
                if candidate >= today:
                    d = candidate
                    break
            if d is None:
                continue
        except (ValueError, TypeError):
            continue
        if r["notify_year"] == d.year:
            continue
        delta = (d - today).days
        if 0 <= delta <= r["remind_days"]:
            user = conn.execute("SELECT telegram_id FROM users WHERE id = ?", (r["user_id"],)).fetchone()
            if user and user["telegram_id"]:
                enqueue_notification(
                    user["telegram_id"], "reminder",
                    f"⏰ Через {delta} дн. важная дата: {r['name']} ({r['date']}). "
                    f"Пора подумать о подарке — подберём сцену из фильма 🎬",
                    url=f"/app/#/dates", conn=conn,
                )
            conn.execute("UPDATE important_dates SET notify_year=? WHERE id=?", (d.year, r["id"]))


async def _send_notifications(bot) -> None:
    conn = connect(config.DB_PATH)
    rows = conn.execute(
        "SELECT * FROM notifications WHERE sent=0 AND (next_attempt_at IS NULL OR datetime(next_attempt_at)<=datetime('now')) ORDER BY created_at LIMIT 50"
    ).fetchall()
    for n in rows:
        try:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            kb = None
            if n["url"]:
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Открыть 🎬", url=config.PUBLIC_URL.rstrip("/") + n["url"]
                                         if n["url"].startswith("/") else n["url"])
                ]])
            await bot.send_message(n["telegram_id"], n["text"], reply_markup=kb)
            conn.execute("UPDATE notifications SET sent=1, sent_at=? WHERE id=?",
                         (now().isoformat(), n["id"]))
            conn.commit()
        except Exception:
            attempts = n["attempts"] + 1
            retry_at = (now() + timedelta(seconds=min(3600, 30 * 2 ** min(attempts, 7)))).isoformat()
            conn.execute("UPDATE notifications SET attempts=?, next_attempt_at=?, sent=? WHERE id=?",
                         (attempts, retry_at, -1 if attempts >= 8 else 0, n["id"]))
            conn.commit()
            log.warning("notification delivery failed id=%s attempt=%s", n["id"], attempts)
    conn.close()


def _gift_soon(conn) -> None:
    """Напоминание дарителю: до отложенного открытия остался 1 день (gift_soon)."""
    soon = (now() + timedelta(hours=24)).isoformat()
    rows = conn.execute(
        "SELECT g.*, u.telegram_id AS giver_tg FROM gifts g LEFT JOIN users u ON u.id=g.giver_id"
        " WHERE g.status='created' AND g.open_at IS NOT NULL AND g.soon_notified=0"
        " AND datetime(g.open_at) > datetime(?) AND datetime(g.open_at) <= datetime(?)", (now().isoformat(), soon,)).fetchall()
    for g in rows:
        if g["giver_tg"]:
            enqueue_notification(
                g["giver_tg"], "gift_soon",
                f"⏳ Завтра откроется ваш подарок для {g['recipient_name']} — сцена из фильма уже ждёт!",
                url=f"/app/#/g/{g['id']}", conn=conn)
        conn.execute("UPDATE gifts SET soon_notified=1 WHERE id=?", (g["id"],))


def _cleanup_media(conn) -> None:
    """Clear inactive attachments after seven days; orphan uploads after 48h.

    Legacy inactive rows without inactive_at fall back to created_at. Files still
    referenced by another gift/reply are retained. Failed unlinks retry next tick.
    """
    import os
    import time
    old = (now() - timedelta(days=7)).isoformat()
    rows = conn.execute(
        "SELECT id FROM gifts WHERE status IN ('expired','deleted') "
        "AND datetime(COALESCE(inactive_at,created_at)) < datetime(?)", (old,)).fetchall()
    retired = set()
    for g in rows:
        for row in conn.execute(
            "SELECT message_voice_file_id, message_photo_file_id, NULL FROM gifts WHERE id=? "
            "UNION ALL SELECT media_path, voice_file_id, photo_file_id FROM replies WHERE gift_id=? "
            "UNION ALL SELECT photo_file_id,NULL,NULL FROM gift_notes WHERE gift_id=?",
            (g["id"], g["id"], g["id"])):
            retired.update(p for p in row if p)
        conn.execute("UPDATE gifts SET message_voice_file_id=NULL, message_photo_file_id=NULL WHERE id=?", (g["id"],))
        conn.execute("UPDATE replies SET media_path=NULL, voice_file_id=NULL, photo_file_id=NULL WHERE gift_id=?", (g["id"],))
        conn.execute("UPDATE gift_notes SET photo_file_id=NULL WHERE gift_id=?", (g["id"],))
    referenced = set()
    for row in conn.execute(
        "SELECT message_voice_file_id a, message_photo_file_id b, NULL c FROM gifts "
        "UNION ALL SELECT media_path, voice_file_id, photo_file_id FROM replies "
        "UNION ALL SELECT photo_file_id,NULL,NULL FROM gift_notes"):
        referenced.update(p for p in row if p)
    root = os.path.realpath(config.MEDIA_DIR)
    for entry in os.scandir(root):
        if f"/media/{entry.name}" in referenced or entry.is_symlink():
            continue
        if entry.is_file() and (f"/media/{entry.name}" in retired or entry.stat().st_mtime < time.time() - 48 * 3600):
            try:
                os.unlink(entry.path)
            except OSError:
                log.exception("media cleanup failed")


def tick(delivery_enabled=False):
    """One tick, independent short transactions; heartbeat records task health."""
    conn = connect(config.DB_PATH)
    failures = 0
    try:
        for task in (_expire_and_ttl, _remind_dates, _gift_soon, _cleanup_media):
            try:
                conn.execute("BEGIN IMMEDIATE")
                task(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                failures += 1
                log.exception("scheduler task %s failed", task.__name__)
        conn.execute("DELETE FROM audit_log WHERE datetime(created_at)<datetime('now',?)",
                     (f"-{config.AUDIT_RETENTION_DAYS} days",))
        conn.execute(
            "INSERT INTO worker_state(name,heartbeat_at,failed_tasks,delivery_enabled) VALUES('scheduler',?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET heartbeat_at=excluded.heartbeat_at,"
            "failed_tasks=excluded.failed_tasks,delivery_enabled=excluded.delivery_enabled",
            (now().isoformat(), failures, int(delivery_enabled)))
        conn.commit()
        return failures
    finally:
        conn.close()


async def run(bot=None) -> None:
    log.info("scheduler started")
    while True:
        try:
            await asyncio.to_thread(tick, bot is not None)
            if config.YUKASSA_SHOP_ID and config.YUKASSA_SECRET_KEY:
                from .payments import recover_creating_payments
                await recover_creating_payments()
            if bot is not None:
                await _send_notifications(bot)
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(config.SCHEDULER_INTERVAL)
