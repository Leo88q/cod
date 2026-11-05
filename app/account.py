"""Self-service erasure of application profile; bookkeeping records are detached.

Backups/provider records are outside this transaction; retention is documented.
Studios must be transferred and pending payments resolved before erasure.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import config
from .db import connect
from .security import request_user

router = APIRouter()


class Erasure(BaseModel):
    confirmation: str
    telegram_id: int | None = None


@router.post("/api/account/delete")
def erase_account(request: Request, body: Erasure):
    if body.confirmation != "УДАЛИТЬ":
        raise HTTPException(400, "Введите УДАЛИТЬ для подтверждения")
    user = request_user(request, body.telegram_id)
    if not user:
        raise HTTPException(401, "Нужно заново открыть приложение из Telegram")
    uid, tg = user["id"], user["telegram_id"]
    conn = connect(config.DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM shops WHERE owner_user_id=?", (uid,)).fetchone():
            raise HTTPException(409, "Сначала передайте студии другому владельцу через поддержку")
        if conn.execute("SELECT 1 FROM payments WHERE user_id=? AND status IN ('pending','creating','verifying_succeeded','verifying_canceled')", (uid,)).fetchone():
            raise HTTPException(409, "Сначала завершите или отмените ожидающие платежи через поддержку")
        owned = "SELECT id FROM gifts WHERE giver_id=?"
        conn.execute(f"DELETE FROM replies WHERE gift_id IN ({owned}) OR respondent_user_id=?", (uid, uid))
        conn.execute(f"DELETE FROM gift_notes WHERE gift_id IN ({owned}) OR author_id=?", (uid, uid))
        conn.execute(f"DELETE FROM consents WHERE gift_id IN ({owned}) OR user_id=?", (uid, uid))
        conn.execute(f"DELETE FROM gift_reports WHERE gift_id IN ({owned}) OR reporter_telegram_id=?", (uid, tg))
        conn.execute(f"DELETE FROM events WHERE ref IN ({owned}) OR telegram_id=?", (uid, tg))
        conn.execute(f"DELETE FROM gift_scenes WHERE gift_id IN ({owned})", (uid,))
        conn.execute(
            "UPDATE gifts SET giver_id=NULL,recipient_name='Удалено',recipient_city=NULL,title=NULL,"
            "occasion=NULL,tone=NULL,message_text=NULL,message_voice_file_id=NULL,message_photo_file_id=NULL,"
            "status='deleted',inactive_at=CURRENT_TIMESTAMP,open_at=NULL,flowwow_ref=NULL WHERE giver_id=?", (uid,))
        for table in ("important_dates", "bookmarks"):
            conn.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM scene_lists WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM notifications WHERE telegram_id=?", (tg,))
        conn.execute("DELETE FROM connections WHERE user_a=? OR user_b=?", (uid, uid))
        conn.execute("UPDATE scenes SET suggested_by=NULL WHERE suggested_by=?", (uid,))
        # Retain financial amounts/provider references, not the app identity.
        conn.execute("UPDATE payments SET user_id=NULL,confirmation_url=NULL WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.commit()
        return {"ok": True, "message": "Профиль удалён. Подарки недоступны. Непривязанные файлы очищаются в течение 48 часов; резервные и финансовые записи — по регламенту хранения."}
    finally:
        conn.close()
