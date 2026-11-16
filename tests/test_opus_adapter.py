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


def test_parse_opus_reads_text_from_time_tail_and_documents():
    """Регрессия: первая версия читала текст только из <w> и теряла реплики.

    В корпусе v2024 встречаются все три раскладки: <w>-токены, tail у <time> и
    текст перед <time>. Молчаливый ноль реплик делал невозможным любой отбор.
    """
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from import_opus_timecodes import parse_opus_documents

    xml = (
        "<opus><documents>"
        "<document id='a'>"
        "<s id='1'><time id='T1' value='00:01:00,000'/>I believe in you</s>"
        "<s id='2'><time id='T2' value='00:01:10,000'/>keep going, dont give up</s>"
        "</document>"
        "<document id='b'>"
        "<s id='1'><time id='T1' value='00:01:00,000'/>I am here</s>"
        "</document>"
        "</documents></opus>"
    )
    import io
    docs = parse_opus_documents(io.BytesIO(xml.encode("utf-8")))
    assert len(docs) == 2, "каждый <document> — отдельное издание (загрузка) субтитров"
    assert [b[3] for b in docs[0]] == ["I believe in you", "keep going, dont give up"]
    assert docs[1][0][1] == 60.0 and docs[1][0][3] == "I am here"


def test_parse_opus_supports_w_tokens_and_text_before_time():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from import_opus_timecodes import parse_opus_documents

    xml = ("<documents><document id='z'>"
           "<s id='1' time_start='00:00:30,000' time_end='00:00:33,000'>"
           "<w>Hang</w> <w>in</w> <w>there</w></s>"
           "<s id='2'>We take the train<time value='00:00:40,000'/><time value='00:00:43,000'/></s>"
           "</document></documents>")
    import io
    docs = parse_opus_documents(io.BytesIO(xml.encode("utf-8")))
    texts = [b[3] for b in docs[0]]
    assert texts[0] == "Hang in there"
    assert texts[1].startswith("We take the train")
    assert docs[0][1][1] == 40.0 and docs[0][1][2] == 43.0
