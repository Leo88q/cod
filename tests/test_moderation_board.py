"""Единый экран модерации: таксономия, жёсткий gate, подтверждение вручную."""
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import connect
from app.intent_taxonomy import (
    categories, category_by_id, fragment_types, intent_for, load_taxonomy,
)
from app.main import app
from app.moderation_board import build_board, seconds_to_label


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_taxonomy_and_scene_intents():
    tax = load_taxonomy()
    assert len(categories()) == 14
    assert tax["gate_threshold"] >= 0.45
    assert tax["min_lexicon_hits"] >= 2
    assert "monologue" in fragment_types()
    assert category_by_id("love_confession")["label"] == "Признание в любви"
    assert seconds_to_label(1976) == "0:32:56"
    assert seconds_to_label(None) == ""
    # старые расплывчатые теги исправлены кураторскими оверрайдами
    assert intent_for("Матрица", 1999)["primary_intent"] == "inspiration_dream"
    assert intent_for("Шрек", 2001)["primary_intent"] == "children_animation"
    assert intent_for("Девчата", 1961)["primary_intent"] == "female_friendship"
    assert intent_for("Москва слезам не верит", 1979)["primary_intent"] == "female_friendship"
    assert intent_for("Служебный роман", 1977)["primary_intent"] == "love_confession"
    assert intent_for("Форрест Гамп", 1994)["primary_intent"] == "support_hope"


def test_board_gates_all_scenes_without_evidence(client):
    conn = connect(config.DB_PATH)
    board = build_board(conn)
    conn.close()
    assert board["stats"]["total"] >= 146
    assert (board["stats"]["needs_curator"] + board["stats"]["pending"]
            + board["stats"]["verified"] == board["stats"]["total"])
    assert len(board["categories"]) == 14
    assert len(board["items"]) == board["stats"]["total"]
    for it in board["items"]:
        assert it["category"] and it["curated_anchor"]
        assert it["proposal_kind"] in ("semantic", "none")
        if it["proposal_kind"] == "none":
            assert it["proposed_label"] == "" and it["proposed_seconds"] is None
            assert it["local_command"] is None and it["open_url"] is None
            assert it["film_links"] and it["film_url"]  # ссылки есть даже без таймкода
            assert all(urlsplit(l["url"]).netloc.lower() in {"vkvideo.ru", "rutube.ru"}
                       for l in it["film_links"])
            assert it["starting_label"] and it["starting_command"]
            assert it["starting_kind"] == "estimate"
            assert it["status"] in ("needs_curator", "verified")
    # у всех 146 сцен свой начальный ориентир, а не один 0:32:56
    assert board["stats"]["with_starting_timecode"] == board["stats"]["total"]
    assert board["stats"]["unique_starting_timecodes"] == board["stats"]["total"]
    starts = [i["starting_seconds"] for i in board["items"]]
    assert len(set(starts)) == len(starts)
    assert sum(1 for x in starts if x == 1976) <= 1
    # ни одной случайной минуты / черновика
    assert all(i["proposal_kind"] != "draft" for i in board["items"])
    assert all(i["proposed_label"] or i["proposal_kind"] == "none" for i in board["items"])


def test_admin_moderation_endpoint_and_confirm(client):
    conn = connect(config.DB_PATH)
    scene = conn.execute(
        "SELECT id, movie_title, movie_year FROM scenes WHERE movie_title=? AND movie_year=?",
        ("Гараж", 1979),
    ).fetchone()
    assert scene is not None
    intent = intent_for(scene["movie_title"], scene["movie_year"])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO timecode_candidates (scene_id, service, timecode_label, timecode_seconds,"
        " timecode_end_label, timecode_end_seconds, source, source_license, evidence_sha1,"
        " line_index, match_score, moment_label, confidence, consensus, auto,"
        " category, fragment_type, evidence_features, reason, intent_score, status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)",
        (scene["id"], "intent", "0:32:56", 1976, "0:33:21", 2001,
         "https://example.com/srt", "test", "sha", 12, 0.4,
         intent["curated_anchor"][:120], 0.62, 1, 0,
         intent["primary_intent"], intent["fragment_type"],
         json.dumps({"lexicon_hits": 3, "tfidf": 0.4}, ensure_ascii=False),
         "Категория: 3 смысловых совпадения, монолог, 25 сек", 0.62, now),
    )
    cid = cur.lastrowid
    conn.commit()
    conn.close()

    r = client.get("/api/admin/moderation", headers={"X-Admin-Token": "test-admin"})
    assert r.status_code == 200
    data = r.json()
    assert data["stats"]["total"] >= 146
    assert data["stats"]["pending"] >= 1
    assert len(data["categories"]) == 14
    item = next(i for i in data["items"] if i["scene_id"] == scene["id"])
    assert item["status"] == "pending" and item["proposed_label"] == "0:32:56"
    assert item["category"] == intent["primary_intent"]
    assert item["reason"] and item["local_command"].startswith("mpv --start=0:32:56")
    assert item["candidate_id"] == cid

    payload = {
        "scene_id": scene["id"], "action": "confirm", "candidate_id": cid,
        "timecode_label": item["proposed_label"], "timecode_end_label": item["proposed_end_label"],
        "version_note": "Мосфильм, реставрация, проверено вручную",
        "moment_label": item["moment_label"], "source_url": item["source_url"],
    }
    r = client.post("/api/admin/moderation/confirm", headers={"X-Admin-Token": "test-admin"},
                    json=dict(payload, version_note=""))
    assert r.status_code == 400
    r = client.post("/api/admin/moderation/confirm", headers={"X-Admin-Token": "test-admin"}, json=payload)
    assert r.status_code == 200 and r.json()["verified"] == 1
    conn = connect(config.DB_PATH)
    row = conn.execute(
        "SELECT verified, timecode_label, timecode_seconds, version_note, primary_intent FROM scenes WHERE id=?",
        (scene["id"],),
    ).fetchone()
    cand = conn.execute("SELECT status, category FROM timecode_candidates WHERE id=?", (cid,)).fetchone()
    conn.close()
    assert row["verified"] == 1
    assert row["timecode_label"] == "0:32:56" and row["timecode_seconds"] == 1976
    assert row["primary_intent"] == intent["primary_intent"]
    assert "Мосфильм" in row["version_note"]
    assert cand["status"] == "approved" and cand["category"] == intent["primary_intent"]


def test_curator_adds_timecode_and_gets_deep_link(client):
    """Куратор вписывает минуту под якорь → карточка получает таймкод и ссылку."""
    conn = connect(config.DB_PATH)
    scene = conn.execute(
        "SELECT id, movie_title, movie_year, verified FROM scenes WHERE movie_title=? AND movie_year=?",
        ("Девчата", 1961),
    ).fetchone()
    assert scene is not None
    conn.close()
    r = client.post(
        "/api/admin/moderation/curate", headers={"X-Admin-Token": "test-admin"},
        json={"scene_id": scene["id"], "timecode_label": "0:17:42",
              "timecode_end_label": "0:18:15", "moment_label": "Разговор подруг",
              "source_url": "https://rutube.ru/video/86bda3b43e24e96bcf93a620862d8236/?t=1062"},
    )
    assert r.status_code == 200
    cid = r.json()["candidate_id"]
    conn = connect(config.DB_PATH)
    cand = conn.execute(
        "SELECT tc.status AS status, tc.category AS category, tc.evidence_features AS evidence_features,"
        " tc.intent_score AS intent_score, tc.timecode_seconds AS timecode_seconds, s.verified AS verified"
        " FROM timecode_candidates tc JOIN scenes s ON s.id=tc.scene_id WHERE tc.id=?", (cid,),
    ).fetchone()
    conn.close()
    assert cand["status"] == "pending" and cand["verified"] == 0
    assert cand["category"] == "female_friendship"
    assert cand["timecode_seconds"] == 1062
    assert json.loads(cand["evidence_features"])["curated"] is True
    assert cand["intent_score"] >= load_taxonomy()["gate_threshold"]

    board = client.get("/api/admin/moderation", headers={"X-Admin-Token": "test-admin"}).json()
    item = next(i for i in board["items"] if i["scene_id"] == scene["id"])
    assert item["status"] == "pending"
    assert item["proposed_label"] == "0:17:42"
    assert item["evidence_kind"] == "curator"
    assert item["open_url"] and "86bda3b43e24e96bcf93a620862d8236" in item["open_url"] and "?t=1062" in item["open_url"]
    assert item["film_links"] and item["film_url"]
    hosts = {urlsplit(l["url"]).netloc.lower() for l in item["film_links"]}
    assert hosts == {"vkvideo.ru", "rutube.ru"}
    assert item["film_links"][0]["url"].startswith("https://vkvideo.ru/")
    assert item["film_links"][1]["url"].startswith("https://rutube.ru/")
