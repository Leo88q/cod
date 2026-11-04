"""Operator controls and transactional audit, without request secrets/content."""
import hashlib
import hmac
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from . import config
from .db import connect
from .security import require_admin, validate_init_data

router = APIRouter()


def audit(conn, request, action, resource, details=None):
    token = request.headers.get('X-Admin-Token', '')
    actor = 'anonymous-dev'
    if config.ADMIN_TOKEN and hmac.compare_digest(token, config.ADMIN_TOKEN):
        actor = 'admin-token'
    else:
        parsed = validate_init_data(request.headers.get('X-Telegram-Init-Data', ''), config.BOT_TOKEN)
        if parsed:
            try:
                uid = json.loads(parsed.get('user', '{}')).get('id')
                if type(uid) is int:
                    actor = 'tg-sha256:' + hashlib.sha256(str(uid).encode()).hexdigest()
            except (ValueError, AttributeError):
                pass
    conn.execute('INSERT INTO audit_log(action,actor,resource,details) VALUES(?,?,?,?)',
                 (action, actor, str(resource), json.dumps(details or {}, ensure_ascii=False)))


def worker_health(conn):
    row = conn.execute("SELECT * FROM worker_state WHERE name='scheduler'").fetchone()
    if not row:
        return {'status': 'missing', 'age_seconds': None}
    stamp = datetime.fromisoformat(row['heartbeat_at'])
    age = max(0, (datetime.now(timezone.utc) - stamp).total_seconds())
    threshold = max(120, config.SCHEDULER_INTERVAL * 3)
    return {'status': 'stale' if age > threshold else 'degraded' if row['failed_tasks'] else 'ok',
            'age_seconds': round(age, 1), 'failed_tasks': row['failed_tasks'],
            'delivery_enabled': bool(row['delivery_enabled'])}


@router.get('/api/admin/audit')
def audit_list(request: Request, limit: int = Query(default=50, ge=1, le=200),
               before: int | None = Query(default=None, ge=1)):
    require_admin(request)
    conn = connect(config.DB_PATH)
    try:
        rows = conn.execute('SELECT * FROM audit_log WHERE (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?',
                            (before, before, limit)).fetchall()
        return {'entries': [dict(r) for r in rows]}
    finally:
        conn.close()


@router.get('/api/admin/notifications/failed')
def failed_notifications(request: Request, limit: int = Query(default=50, ge=1, le=200)):
    require_admin(request)
    conn = connect(config.DB_PATH)
    try:
        # No recipient ID, message text or gift URLs in this operational view.
        rows = conn.execute('SELECT id,kind,attempts,created_at,next_attempt_at FROM notifications '
                            'WHERE sent=-1 ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        return {'notifications': [dict(r) for r in rows]}
    finally:
        conn.close()


@router.post('/api/admin/notifications/{notification_id}/retry')
def retry_notification(notification_id: int, request: Request):
    require_admin(request)
    conn = connect(config.DB_PATH)
    try:
        conn.execute('BEGIN IMMEDIATE')
        cur = conn.execute('UPDATE notifications SET sent=0,attempts=0,next_attempt_at=NULL WHERE id=? AND sent=-1',
                           (notification_id,))
        if not cur.rowcount:
            raise HTTPException(409, 'Повтор возможен только для уведомления с исчерпанными попытками')
        audit(conn, request, 'notification_retry', notification_id)
        conn.commit()
        return {'ok': True}
    finally:
        conn.close()
