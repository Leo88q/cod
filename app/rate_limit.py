"""Atomic fixed-window quotas shared by processes using the same SQLite DB.

Only signed Telegram identities count as users; an IP quota always applies too.
For multiple hosts with independent databases use a Redis-backed implementation.
"""
import hashlib
import json
import math
import time

from . import config
from .db import connect
from .security import validate_init_data


def verified_subject(init_data: str):
    parsed = validate_init_data(init_data, config.BOT_TOKEN)
    if not parsed:
        return None
    try:
        user = json.loads(parsed.get("user", "{}"))
        uid = user.get("id")
        if type(uid) is int and uid > 0:
            return f"tg:{uid}"
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def consume(db_path, quotas, timestamp=None, window=60):
    """Atomically consume all (subject, scope, limit) quotas, or none.

    Returns seconds until reset on denial, zero on success. Raw IPs/IDs are
    hashed; expired windows are deleted to bound storage.
    """
    current = time.time() if timestamp is None else timestamp
    bucket = int(current // window)
    retry = max(1, math.ceil((bucket + 1) * window - current))
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM rate_limit_buckets WHERE bucket < ?", (bucket,))
        keys = []
        for subject, scope, limit in quotas:
            key = hashlib.sha256(subject.encode()).hexdigest()
            row = conn.execute(
                "SELECT hits FROM rate_limit_buckets WHERE subject=? AND scope=? AND bucket=?",
                (key, scope, bucket)).fetchone()
            if row and row[0] >= limit:
                conn.rollback()
                return retry
            keys.append((key, scope, bucket))
        for key in keys:
            conn.execute(
                "INSERT INTO rate_limit_buckets(subject,scope,bucket,hits) VALUES (?,?,?,1) "
                "ON CONFLICT(subject,scope,bucket) DO UPDATE SET hits=hits+1", key)
        conn.commit()
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
