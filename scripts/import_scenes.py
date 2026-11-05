"""Импорт сцен в каталог из CSV.

Использование:
    .venv/bin/python scripts/import_scenes.py db/scenes_template.csv

Колонки CSV (см. db/scenes_template.csv):
    movie_title,movie_year,imdb_id,tmdb_id,service,timecode_label,timecode_seconds,genre,era,age_rating,
    mood_tags,occasion_tags,description,why_fits,status
Теги — через точку с запятой ("романтика; юмор"). status по умолчанию draft (на модерацию).
Описание — только наш пересказ, без дословных реплик (авторское право).
"""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config  # noqa: E402
from app.db import connect, init_db  # noqa: E402

FIELDS = ["movie_title", "movie_year", "imdb_id", "tmdb_id", "service", "timecode_label", "timecode_seconds",
          "genre", "era", "age_rating", "mood_tags", "occasion_tags", "description",
          "why_fits", "status"]


def _tags(s: str) -> str:
    return json.dumps([t.strip() for t in (s or "").split(";") if t.strip()], ensure_ascii=False)


def import_scenes(csv_path: str, db_path: str | None = None) -> int:
    db_path = db_path or config.DB_PATH
    init_db(db_path)
    conn = connect(db_path)
    n = 0
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not (row.get("movie_title") or "").strip():
                continue
            conn.execute(
                "INSERT INTO scenes (movie_title, movie_year, imdb_id, tmdb_id, service, timecode_label,"
                " timecode_seconds, genre, era, age_rating, mood_tags, occasion_tags, description,"
                " why_fits, source, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'import_csv', ?)",
                (
                    row["movie_title"].strip(),
                    int(row["movie_year"]) if (row.get("movie_year") or "").strip() else None,
                    (row.get("imdb_id") or "").strip() or None,
                    (row.get("tmdb_id") or "").strip() or None,
                    (row.get("service") or "").strip() or None,
                    (row.get("timecode_label") or "").strip() or None,
                    int(row["timecode_seconds"]) if (row.get("timecode_seconds") or "").strip() else None,
                    (row.get("genre") or "").strip() or None,
                    (row.get("era") or "").strip() or None,
                    (row.get("age_rating") or "").strip() or None,
                    _tags(row.get("mood_tags", "")),
                    _tags(row.get("occasion_tags", "")),
                    (row.get("description") or "").strip(),
                    (row.get("why_fits") or "").strip() or None,
                    (row.get("status") or "draft").strip() or "draft",
                ),
            )
            n += 1
    conn.commit()
    conn.close()
    return n


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python scripts/import_scenes.py <csv>")
        sys.exit(1)
    print(f"Импортировано сцен: {import_scenes(sys.argv[1])}")
