"""Сквозной сценарий: доказательство -> публикация без человека -> выдача в API.

Проверяем главное обещание: сцена, у которой момент сошёлся в двух независимых
изданиях, получает таймкод в приложении БЕЗ ручной сверки, при этом флаг
`verified` (человеческая проверка) не подменяется автоматикой.
"""
import json
import random
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config
from app import evidence_engine as E
from app.db import init_db


def _use_db(monkeypatch, path: str) -> None:
    """Переключить всё приложение на тестовую БД.

    `main.py` импортирует `connect` по значению, поэтому одной подмены
    `config.DB_PATH` недостаточно — подменяем сам `connect` в модуле. Подмена
    идёт через monkeypatch, иначе она протекает в другие тесты и весь набор
    начинает ходить в чужую БД.
    """
    from app import db as db_module
    from app import main as main_module
    monkeypatch.setattr(main_module, "connect", lambda _ignored=None: db_module.connect(path))


def _blocks(rows):
    return [(i + 1, s, e, t) for i, (s, e, t) in enumerate(rows)]


def _movie(anchor_text: str, anchor_at: float, seed: int):
    rnd = random.Random(seed)
    vocab = ["дорога", "поезд", "письмо", "мать", "небо", "город", "дверь", "окно",
             "утро", "вечер", "стол", "стакан", "улица", "снег", "ветер", "море"]
    rows, t, idx = [], 130.0, 0
    while t < anchor_at - 40:
        rows.append((t, t + 3, " ".join(rnd.choice(vocab) for _ in range(9))))
        t += 6.0
        idx += 1
    for part in anchor_text.split("|"):
        rows.append((t, t + 9, part.strip()))
        t += 9.5
    while t < anchor_at + 1200:
        rows.append((t, t + 3, " ".join(rnd.choice(vocab) for _ in range(9))))
        t += 6.0
        idx += 1
    return E.make_windows(_blocks(rows))


LEX = E.Lexicon.from_terms(
    ["люблю", "признаюсь", "чувства"], ["love", "confess", "feelings", "my heart"])


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "evidence.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    init_db(path)
    _use_db(monkeypatch, path)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO scenes (movie_title, movie_year, description, status, primary_intent,"
        " fragment_type, mood_tags, occasion_tags, created_at)"
        " VALUES ('Тестовый фильм', 2001, 'Проверка доказательного отбора.', 'approved',"
        " 'love_confession', 'dialogue', '[]', '[]', datetime('now'))")
    conn.commit()
    conn.close()
    return path


def test_evidence_publication_reaches_api_without_human(db):
    from app.main import app

    text = "I love you|my heart is yours|I confess my feelings"
    editions = [E.Edition("edition-a", "en", _movie(text, 800, seed=1)),
                E.Edition("edition-b", "en", _movie(text, 1100, seed=2))]
    cand = E.select_scene("Тестовый фильм", 2001,
                          {"primary_intent": "love_confession", "fragment_type": "dialogue"},
                          LEX, editions)
    assert cand is not None and cand.verdict == "auto"

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    scene = conn.execute("SELECT id FROM scenes WHERE movie_title='Тестовый фильм'").fetchone()
    sid = scene["id"]
    report = json.dumps({
        "verdict": cand.verdict, "consensus": cand.consensus,
        "editions": cand.editions,
        "matches": [{"edition": m.edition, "ratio": m.ratio, "skew": m.skew} for m in cand.matches],
    }, ensure_ascii=False)
    conn.execute(
        "INSERT INTO timecode_candidates (scene_id, service, timecode_label, timecode_seconds,"
        " timecode_end_label, timecode_end_seconds, source, source_license, evidence_sha1,"
        " match_score, moment_label, confidence, consensus, auto, category, status, created_at,"
        " evidence_report)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'approved',datetime('now'),?)",
        (sid, "evidence", "0:13:20", int(cand.anchor.start), "0:13:47", int(cand.anchor.end),
         "edition-a", "test", cand.sha1, cand.score, cand.reason, cand.score,
         cand.consensus, 1, cand.intent, report))
    # публикация: таймкод у сцены есть, verified (человек) — НЕТ
    conn.execute(
        "UPDATE scenes SET timecode_label=?, timecode_seconds=?, timecode_end_label=?,"
        " timecode_end_seconds=?, evidence_kind='auto', evidence_editions=?,"
        " evidence_report=? WHERE id=?",
        ("0:13:20", int(cand.anchor.start), "0:13:47", int(cand.anchor.end),
         cand.consensus, report, sid))
    conn.commit()
    verified = conn.execute("SELECT verified, evidence_kind FROM scenes WHERE id=?", (sid,)).fetchone()
    conn.close()
    assert verified["verified"] == 0, "автоматика не должна выдавать себя за проверку человеком"
    assert verified["evidence_kind"] == "auto"

    client = TestClient(app)
    # каталог большой: ищем по названию, иначе сцена не попадёт в первые 50
    found = client.post("/api/scenes/search", json={"query": "Тестовый", "limit": 50}).json()["scenes"]
    item = next(s for s in found if s["id"] == sid)
    assert item["timecode_label"] is not None
    # и она же видна в фильтре «с таймкодом» — то есть пользователь может её выбрать
    with_tc = client.post("/api/scenes/search", json={"query": "Тестовый",
                                                      "has_timecode": True}).json()
    assert with_tc["total"] == 1 and with_tc["scenes"][0]["id"] == sid
    assert item["published_by_evidence"] is True
    assert item["evidence_editions"] == 2

    meta = client.get("/api/scenes/meta").json()
    assert meta["published_by_evidence"] >= 1

    moments = client.get(f"/api/scenes/{sid}/moments").json()
    assert any(m["timecode_label"] == "0:13:20" for m in moments["moments"])


def test_single_edition_stays_in_review_queue(db):
    """Одно издание: сцена НЕ получает таймкод, кандидат уходит в pending."""
    text = "I love you|my heart is yours|I confess my feelings"
    cand = E.select_scene("Тестовый фильм", 2001,
                          {"primary_intent": "love_confession", "fragment_type": "dialogue"},
                          LEX, [E.Edition("only-one", "en", _movie(text, 900, seed=3))])
    assert cand is not None
    assert cand.verdict != "auto"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    scene = conn.execute("SELECT timecode_seconds, evidence_kind FROM scenes"
                         " WHERE movie_title='Тестовый фильм'").fetchone()
    conn.close()
    assert scene["timecode_seconds"] is None and scene["evidence_kind"] is None
