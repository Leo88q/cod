"""Validate a compressed SQLite backup in isolation; never overwrite live data.

python scripts/verify_backup.py backups/scena_....db.gz
"""
import gzip
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def verify(path):
    with tempfile.TemporaryDirectory(prefix="scena-restore-") as directory:
        restored = Path(directory) / "restored.db"
        with gzip.open(path, "rb") as source, restored.open("wb") as target:
            shutil.copyfileobj(source, target)
        with sqlite3.connect(f"file:{restored}?mode=ro", uri=True) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "Integrity check failed"
            assert not conn.execute("PRAGMA foreign_key_check").fetchall(), "Foreign key check failed"
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"users", "gifts", "payments"} <= tables, "Missing application tables"
        print("RESTORE CHECK OK (temporary copy; live database unchanged)")


if __name__ == "__main__":
    verify(sys.argv[1])
