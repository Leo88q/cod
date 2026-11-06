"""Отдельный блок №1: 50 подготовленных romantic-отрывков на модерации."""
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import connect
from app.main import app
from app.moderation_board import build_board

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACT = os.path.join(BASE, "data", "timecode_library", "love_confession_batch.json")
CURATED = os.path.join(BASE, "data", "timecode_library", "love_confession_curated.json")
DOC = os.path.join(BASE, "docs", "20-love-confession-batch.md")
BATCH_ID = "love_confession_50"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def inserted_batch():
    """Две строки блока в изолированной тестовой БД; после теста удаляются."""
    data = json.load(open(ARTIFACT, encoding="utf-8"))
    conn = connect(config.DB_PATH)
    conn.execute("DELETE FROM timecode_candidates WHERE batch_id=?", (BATCH_ID,))
    scene_ids = {
        (r["movie_title"], r["movie_year"]): r["id"]
        for r in conn.execute("SELECT id, movie_title, movie_year FROM scenes").fetchall()
    }
    usable = [c for c in data["entries"] if (c["movie_title"], c["movie_year"]) in scene_ids]
    srt = next(c for c in usable if c["evidence_kind"] == "srt_window")
    orienteer = next(c for c in usable if c["evidence_kind"] == "starting_orienteer")
    external = next(c for c in usable if c["evidence_kind"] == "external_orienteer")
    selected = [srt, orienteer, external]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    columns = (
        "scene_id, service, timecode_label, timecode_seconds, timecode_end_label,"
        " timecode_end_seconds, source, source_license, evidence_sha1, line_index,"
        " match_score, moment_label, confidence, consensus, auto, category,"
        " fragment_type, evidence_features, reason, intent_score, status,"
        " created_at, batch_id, batch_rank"
    )
    placeholders = ",".join(["?"] * 24)
    ids = []
    for c in selected:
        values = (
            scene_ids[(c["movie_title"], c["movie_year"])], "love_batch",
            c["timecode_label"], c["timecode_seconds"], c["timecode_end_label"],
            c["timecode_end_seconds"], c["source"], c["source_license"],
            c["evidence_sha1"], c["line_index"], c["match_score"],
            c["moment_label"], c["confidence"], c["consensus"], 0,
            c["category"], c["fragment_type"],
            json.dumps(c["evidence_features"], ensure_ascii=False), c["context"],
            c["intent_score"], "pending", now, BATCH_ID, c["batch_rank"],
        )
        cur = conn.execute(
            f"INSERT INTO timecode_candidates ({columns}) VALUES ({placeholders})", values)
        ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    yield selected, ids
    conn = connect(config.DB_PATH)
    conn.execute("DELETE FROM timecode_candidates WHERE batch_id=?", (BATCH_ID,))
    conn.commit()
    conn.close()


def test_batch_artifact_is_prepared_not_verified():
    data = json.load(open(ARTIFACT, encoding="utf-8"))
    curated = json.load(open(CURATED, encoding="utf-8"))
    doc = open(DOC, encoding="utf-8").read()
    assert data["batch_id"] == BATCH_ID
    assert data["stats"]["count"] == 50
    assert data["stats"]["unique_scenes"] == 50
    assert data["stats"]["srt_evidence"] + data["stats"]["starting_orienteers"] == 50
    assert data["stats"]["external_orienteers"] == 2
    assert sum(1 for c in data["entries"] if c["evidence_kind"] == "external_orienteer"
               and c.get("external_time")) == 2
    assert data["rules"]["verified_by_human"] is False
    assert data["rules"]["version_note_required"] is True
    assert data["rules"]["status"] == "pending"
    assert len(curated["moments"]) == 50
    keys = {(c["movie_title"], c["movie_year"]) for c in data["entries"]}
    assert len(keys) == 50
    for c in data["entries"]:
        assert c["batch_id"] == BATCH_ID and c["verified"] == 0
        assert c["timecode_seconds"] > 0 and c["timecode_label"] != "0:00:00"
        assert c["timecode_end_seconds"] > c["timecode_seconds"]
        assert c["evidence_kind"] in ("srt_window", "external_orienteer", "starting_orienteer")
        assert c["moment_label"] and c["context"] and c["emotion_tags"]
        assert c["source"] and c["source_license"] and c["evidence_sha1"]
        assert c["version_hint"] and "version_note" in c["version_hint"]
        assert c["evidence_source"] != ""
    assert "💘 Блок: признание в любви (50)" in doc
    assert "не проверенная библиотека" in doc
    assert "проверено вручную: 0" in doc
    assert "Локальный SRT" in doc and "Начальный ориентир" in doc


def test_love_batch_endpoint_and_main_board_exclusion(client, inserted_batch):
    selected, candidate_ids = inserted_batch
    headers = {"X-Admin-Token": "test-admin"}
    r = client.get("/api/admin/moderation/love-confession", headers=headers)
    assert r.status_code == 200
    batch = r.json()
    assert batch["batch_id"] == BATCH_ID
    assert batch["label"] == "Признание в любви и чувства"
    assert len(batch["items"]) == 3
    assert batch["stats"]["total"] == 3
    assert batch["stats"]["srt_evidence"] == 1
    assert batch["stats"]["starting_orienteers"] == 2
    assert batch["stats"]["external_orienteers"] == 1
    assert batch["stats"]["verified"] == 0
    assert batch["stats"]["verified_by_human"] is False
    assert batch["stats"]["version_note_required"] is True
    assert {i["candidate_id"] for i in batch["items"]} == set(candidate_ids)
    for item in batch["items"]:
        assert item["status"] == "pending"
        assert item["batch_id"] == BATCH_ID
        assert item["proposed_label"] and item["proposed_seconds"] > 0
        assert item["moment_label"] and item["context"] and item["emotion_tags"]
        assert item["source_license"] and item["evidence_sha1"]
        assert item["evidence_note"] and item["version_hint"]
        assert item["local_command"].startswith("mpv --start=")
        assert item["source_label"] in ("Локальный SRT", "Начальный ориентир", "Внешний ориентир")
        assert item["evidence_kind"] in ("srt_window", "external_orienteer", "starting_orienteer")
        assert item["status_label"] in ("есть таймкод — к отсмотру", "есть ориентир — к отсмотру")
        assert item["film_links"] and item["open_url"]
        assert all(urlsplit(l["url"]).netloc.lower() in {"vkvideo.ru", "rutube.ru"}
                   for l in item["film_links"])
        assert item["youtube_search_url"] is None and item["imdb_url"] is None
    orienteer = next(i for i in batch["items"] if i["evidence_kind"] == "starting_orienteer")
    assert orienteer["curated_orienteer"] is True
    assert orienteer["starting_kind"] == "estimate"
    assert "не проверенный момент" in orienteer["starting_note"].lower()
    external = next(i for i in batch["items"] if i["evidence_kind"] == "external_orienteer")
    assert external["source_label"] == "Внешний ориентир"
    assert external["starting_kind"] == "external"
    assert external["external_refs"]

    conn = connect(config.DB_PATH)
    board = build_board(conn)
    verified_rows = conn.execute(
        f"SELECT verified FROM scenes WHERE id IN ({','.join('?' * len(batch['items']))})",
        tuple(i["scene_id"] for i in batch["items"]),
    ).fetchall()
    conn.close()
    assert all(row["verified"] == 0 for row in verified_rows)
    assert all(i["candidate_id"] not in candidate_ids for i in board["items"])
    assert all(i["proposal_kind"] != "love_batch" for i in board["items"])

    item = batch["items"][0]
    no_note = client.post("/api/admin/moderation/confirm", headers=headers, json={
        "scene_id": item["scene_id"], "action": "confirm",
        "candidate_id": item["candidate_id"],
        "timecode_label": item["proposed_label"],
        "timecode_end_label": item["proposed_end_label"],
    })
    assert no_note.status_code == 400
    assert "version_note" in no_note.text
