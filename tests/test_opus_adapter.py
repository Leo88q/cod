"""Тест адаптера OPUS / OpenSubtitles v2024 на синтетическом XML-архиве."""
import json
import os
import sqlite3
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "import_opus_timecodes.py")

XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<document>
  <s time_start="00:00:10,000" time_end="00:00:13,000"><w>Run</w><w>Forrest</w><w>run</w></s>
  <s time_start="00:01:10,000" time_end="00:01:14,000"><w>Mama</w><w>always</w><w>said</w><w>life</w></s>
  <s time_start="00:02:10,000" time_end="00:02:13,000"><w>Chocolates</w><w>box</w><w>dream</w></s>
  <s><time value="00:03:10,000"/><w>Feather</w><w>sky</w><w>home</w><time value="00:03:14,000"/></s>
</document>
"""


def _make_zip(path: str) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        # путь вида en/<imdb>/<imdb>.xml, как в OPUS v2024
        zf.writestr("en/0109830/0109830.xml", XML)
    return path


def test_opus_adapter_inserts_pending_time_facts_only(tmp_path):
    db = str(tmp_path / "scena.db")
    from app.db import init_db
    init_db(db)
    zip_path = _make_zip(str(tmp_path / "en.zip"))
    proc = subprocess.run(
        [sys.executable, SCRIPT, "--opus-dir", str(tmp_path), "--db", db,
         "--limit-files", "1", "--max-per-scene", "2"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "обработано XML: 1" in proc.stdout
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT tc.*, s.movie_title FROM timecode_candidates tc JOIN scenes s ON s.id=tc.scene_id"
    ).fetchall()
    assert rows, "должны появиться кандидаты"
    assert all(r["status"] == "pending" for r in rows)
    assert all(r["verified"] == 0 if "verified" in r.keys() else True for r in rows)
    r = rows[0]
    assert r["movie_title"] == "Форрест Гамп"
    assert r["timecode_label"] and r["timecode_seconds"] is not None
    assert r["timecode_end_label"] and r["timecode_end_seconds"] is not None
    assert len(r["evidence_sha1"]) == 40
    assert r["source"].startswith("opus://en.zip/")
    assert "ODC-BY" in r["source_license"]
    assert r["moment_label"]
    # текста реплик в БД нет
    dump = conn.execute("SELECT group_concat(timecode_label || ' ' || coalesce(moment_label,'') || ' ' || source, ' ') FROM timecode_candidates").fetchone()[0]
    assert "Run" not in dump and "Chocolates" not in dump and "Feather" not in dump
    conn.close()


def test_opus_dry_run_does_not_write(tmp_path):
    db = str(tmp_path / "scena.db")
    from app.db import init_db
    init_db(db)
    _make_zip(str(tmp_path / "en.zip"))
    proc = subprocess.run(
        [sys.executable, SCRIPT, "--opus-dir", str(tmp_path), "--db", db,
         "--limit-files", "1", "--dry-run"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[dry]" in proc.stdout
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM timecode_candidates").fetchone()[0] == 0
    conn.close()
