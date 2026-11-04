"""Экспорт одобренных сцен каталога в CSV (для модерации/партнёров/бэкапов).

Использование:
    .venv/bin/python scripts/export_scenes.py out.csv [--status approved]
"""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config  # noqa: E402
from app.db import connect, init_db  # noqa: E402

FIELDS = ["id", "movie_title", "movie_year", "service", "version_note", "timecode_label",
          "timecode_seconds", "watch_url", "genre", "era", "age_rating", "mood_tags",
          "occasion_tags", "description", "why_fits", "source", "source_license", "status",
          "verified", "verified_at", "popularity", "created_at"]


def export_scenes(out_path: str, status: str = "approved", db_path: str | None = None) -> int:
    db_path = db_path or config.DB_PATH
    init_db(db_path)
    conn = connect(db_path)
    rows = conn.execute("SELECT * FROM scenes WHERE status=? ORDER BY id", (status,)).fetchall()
    conn.close()
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            d = dict(r)
            for k in ("mood_tags", "occasion_tags"):
                try:
                    d[k] = ";".join(json.loads(d[k] or "[]"))
                except json.JSONDecodeError:
                    pass
            w.writerow({k: d.get(k) for k in FIELDS})
    return len(rows)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "scenes_export.csv"
    status = "approved"
    if "--status" in sys.argv:
        status = sys.argv[sys.argv.index("--status") + 1]
    print(f"Выгружено сцен ({status}): {export_scenes(out, status)} -> {out}")
