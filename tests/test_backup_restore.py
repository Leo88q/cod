"""Чек-лист P0: пробное восстановление бэкапа выполняется автоматически в pytest/CI.

Живая БД не трогается: создаём временную БД, снимаем WAL-safe копию,
упаковываем её и проверяем скриптом `scripts/verify_backup.py` в изоляции.
"""
import gzip
import os
import shutil
import sqlite3
import subprocess
import sys

from app.db import init_db

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERIFY = os.path.join(BASE, "scripts", "verify_backup.py")


def test_backup_restores_to_a_clean_temporary_copy(tmp_path):
    live = tmp_path / "scena.db"
    init_db(str(live))
    src = sqlite3.connect(str(live))
    scenes_before = src.execute("SELECT COUNT(*) FROM scenes").fetchone()[0]
    assert scenes_before > 0

    plain = tmp_path / "backup.db"
    dst = sqlite3.connect(str(plain))
    src.backup(dst)  # WAL-safe консистентная копия
    dst.close()
    src.close()

    packed = tmp_path / "backup.db.gz"
    with plain.open("rb") as raw, gzip.open(packed, "wb") as zipped:
        shutil.copyfileobj(raw, zipped)

    result = subprocess.run(
        [sys.executable, VERIFY, str(packed)],
        cwd=BASE, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "RESTORE CHECK OK" in result.stdout
    assert "live database unchanged" in result.stdout

    # Временная «живая» БД не изменилась ни размером, ни содержимым.
    src = sqlite3.connect(str(live))
    assert src.execute("SELECT COUNT(*) FROM scenes").fetchone()[0] == scenes_before
    assert src.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    src.close()
