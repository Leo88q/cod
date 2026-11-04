"""Mutual consent to connections; never grants access to private user collections."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import config
from .db import connect
from .security import request_user

router = APIRouter()


class Invitation(BaseModel):
    telegram_id: int | None = None
    peer_telegram_id: int


def actor(request, telegram_id):
    user = request_user(request, telegram_id)
    if not user:
        raise HTTPException(401, "Нужен Telegram-аккаунт")
    return user


@router.post("/api/connections")
def invite(request: Request, body: Invitation):
    user = actor(request, body.telegram_id)
    if body.peer_telegram_id == user["telegram_id"]:
        raise HTTPException(400, "Нельзя связать таймлайн с самим собой")
    conn = connect(config.DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")
        peer = conn.execute("SELECT id FROM users WHERE telegram_id=?", (body.peer_telegram_id,)).fetchone()
        if not peer:
            raise HTTPException(404, "Пользователь не найден")
        a, b = sorted((user["id"], peer["id"]))
        row = conn.execute("SELECT * FROM connections WHERE user_a=? AND user_b=?", (a, b)).fetchone()
        if row:
            # A reciprocal POST is not acceptance; require the explicit accept action.
            if row["requested_by"] is None and row["status"] == "pending":
                conn.execute("UPDATE connections SET requested_by=? WHERE id=?", (user["id"], row["id"]))
            result = {"id": row["id"], "status": row["status"]}
        else:
            cur = conn.execute("INSERT INTO connections(user_a,user_b,status,requested_by) VALUES(?,?,'pending',?)",
                               (a, b, user["id"]))
            result = {"id": cur.lastrowid, "status": "pending"}
        conn.commit()
        return {"ok": True, **result}
    finally:
        conn.close()


@router.get("/api/connections")
def invitations(request: Request, telegram_id: int | None = None):
    user = actor(request, telegram_id)
    conn = connect(config.DB_PATH)
    try:
        rows = conn.execute(
            "SELECT c.id,c.status,c.requested_by,u.telegram_id,u.first_name FROM connections c "
            "JOIN users u ON u.id=CASE WHEN c.user_a=? THEN c.user_b ELSE c.user_a END "
            "WHERE ? IN(c.user_a,c.user_b)", (user["id"], user["id"])).fetchall()
        return {"connections": [{"id": r["id"], "status": r["status"],
                 "peer_telegram_id": r["telegram_id"], "peer_name": r["first_name"],
                 "incoming": r["requested_by"] is not None and r["requested_by"] != user["id"]} for r in rows]}
    finally:
        conn.close()


@router.post("/api/connections/{connection_id}/accept")
def accept(connection_id: int, request: Request, telegram_id: int | None = None):
    user = actor(request, telegram_id)
    conn = connect(config.DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM connections WHERE id=?", (connection_id,)).fetchone()
        if not row or user["id"] not in (row["user_a"], row["user_b"]):
            raise HTTPException(404, "Запрос не найден")
        if row["requested_by"] is None or row["requested_by"] == user["id"]:
            raise HTTPException(403, "Подтвердить может только получатель запроса")
        conn.execute("UPDATE connections SET status='accepted' WHERE id=?", (connection_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/api/connections/{connection_id}")
def disconnect(connection_id: int, request: Request, telegram_id: int | None = None):
    user = actor(request, telegram_id)
    conn = connect(config.DB_PATH)
    try:
        conn.execute("DELETE FROM connections WHERE id=? AND ? IN(user_a,user_b)", (connection_id, user["id"]))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
