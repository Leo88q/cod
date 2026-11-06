"""Блок №1: компактная таблица модератора на 50 фильмов «признание в любви и чувства».

Проверяем три вещи: подготовленный JSON (50 строк, 9 точных ссылок, 0 проверенных),
новый `/table` без поисковых URL и редактирование строки без `verified=1`.
"""
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import connect, init_db
from app.main import app
from app.moderation_board import build_board

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACT = os.path.join(BASE, "data", "timecode_library", "love_confession_batch.json")
CURATED = os.path.join(BASE, "data", "timecode_library", "love_confession_curated.json")
DOC = os.path.join(BASE, "docs", "20-love-confession-batch.md")
BATCH_ID = "love_confession_50"
HEADERS = {"X-Admin-Token": "test-admin"}


@pytest.fixture(scope="module")
def isolated_db(tmp_path_factory):
    """Своя БД: тесты не трогают рабочий каталог сцен."""
    mp = pytest.MonkeyPatch()
    path = str(tmp_path_factory.mktemp("love") / "scena.db")
    mp.setattr(config, "DB_PATH", path)
    init_db(path)
    yield path
    mp.undo()


@pytest.fixture(scope="module")
def client(isolated_db):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def inserted_batch(isolated_db):
    """Три строки блока: с прямой ссылкой, начальный и внешний ориентир."""
    data = json.load(open(ARTIFACT, encoding="utf-8"))
    conn = connect(isolated_db)
    conn.execute("DELETE FROM timecode_candidates WHERE batch_id=?", (BATCH_ID,))
    scene_ids = {
        (r["movie_title"], r["movie_year"]): r["id"]
        for r in conn.execute("SELECT id, movie_title, movie_year FROM scenes").fetchall()
    }
    usable = [c for c in data["entries"] if (c["movie_title"], c["movie_year"]) in scene_ids]
    direct = next(c for c in usable if c.get("film_url"))
    starting = next(c for c in usable if c["evidence_kind"] == "starting_orienteer")
    external = next(c for c in usable if c["evidence_kind"] == "external_orienteer")
    selected = [direct, starting, external]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    columns = (
        "scene_id, service, timecode_label, timecode_seconds, timecode_end_label,"
        " timecode_end_seconds, source, source_license, evidence_sha1, line_index,"
        " match_score, moment_label, confidence, consensus, auto, category,"
        " fragment_type, evidence_features, reason, intent_score, status,"
        " created_at, batch_id, batch_rank, film_url, version_note"
    )
    placeholders = ",".join(["?"] * 26)
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
            c.get("film_url", ""), "",
        )
        cur = conn.execute(
            f"INSERT INTO timecode_candidates ({columns}) VALUES ({placeholders})", values)
        ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    yield selected, ids
    conn = connect(isolated_db)
    conn.execute("DELETE FROM timecode_candidates WHERE batch_id=?", (BATCH_ID,))
    conn.commit()
    conn.close()


def test_batch_artifact_is_prepared_not_verified():
    data = json.load(open(ARTIFACT, encoding="utf-8"))
    curated = json.load(open(CURATED, encoding="utf-8"))
    doc = open(DOC, encoding="utf-8").read()
    entries = data["entries"]
    assert data["batch_id"] == BATCH_ID
    assert data["stats"]["count"] == 50
    assert data["stats"]["unique_scenes"] == 50
    assert data["stats"]["srt_evidence"] == 13
    assert data["stats"]["starting_orienteers"] == 37
    assert data["stats"]["external_orienteers"] == 13
    assert data["stats"]["with_direct_links"] == 9
    assert sum(1 for c in entries if c["evidence_kind"] == "external_orienteer"
               and c.get("external_time")) == 13
    assert data["rules"]["verified_by_human"] is False
    assert data["rules"]["verified"] == 0
    assert data["rules"]["version_note_required"] is True
    assert data["rules"]["status"] == "pending"
    assert len(curated["moments"]) == 50
    assert sum(1 for v in curated["moments"].values() if v.get("runtime_minutes")) == 50

    keys = {(c["movie_title"], c["movie_year"]) for c in entries}
    starts = {c["timecode_seconds"] for c in entries}
    assert len(keys) == 50 and len(starts) == 50
    for c in entries:
        assert c["batch_id"] == BATCH_ID and c["verified"] == 0
        assert 1 <= c["batch_rank"] <= 50
        assert 0 < c["timecode_seconds"] < c["timecode_end_seconds"] <= c["runtime_seconds"]
        assert c["timecode_label"] != "0:00:00"
        assert isinstance(c.get("film_url", ""), str)
        assert c["runtime_seconds"] > 0
        if c.get("film_url"):
            host = urlsplit(c["film_url"]).netloc.lower()
            assert host in {"vkvideo.ru", "rutube.ru"}
            assert "/video" in urlsplit(c["film_url"]).path
            assert "search" not in c["film_url"]
    assert sum(1 for c in entries if c.get("film_url")) == 9
    assert sum(1 for c in entries if not c.get("film_url")) == 41

    assert "50 фильмов · 9 прямых ссылок VK/Rutube · 13 внешних ориентиров · проверено вручную: 0" in doc
    assert "поле под ссылку" in doc
    assert "## Как проверять строку" in doc
    assert "Один компактный экран на 50 строк" in doc
    assert "💘 Блок: признание в любви (50)" not in doc
    assert "не проверенная библиотека" not in doc


def test_love_table_endpoint_has_direct_links_and_editable_rows(client, inserted_batch):
    selected, candidate_ids = inserted_batch
    r = client.get("/api/admin/moderation/love-confession/table", headers=HEADERS)
    assert r.status_code == 200
    table = r.json()
    assert table["batch_id"] == BATCH_ID
    assert table["label"] == "Признание в любви и чувства"
    assert len(table["items"]) == 3
    assert table["stats"]["total"] == 3
    assert table["stats"]["direct_links"] == 1
    assert table["stats"]["empty_link_fields"] == 2
    assert table["stats"]["pending"] == 3
    assert table["stats"]["verified"] == 0
    assert table["stats"]["version_note_required"] is True
    assert table["stats"]["verified_by_human"] is False
    assert {i["candidate_id"] for i in table["items"]} == set(candidate_ids)
    ranks = [i["batch_rank"] for i in table["items"]]
    assert ranks == sorted(ranks)
    for item in table["items"]:
        assert item["status"] == "pending" and item["status_label"] == "к проверке"
        assert item["verified"] is False
        assert item["timecode_label"] and item["timecode_end_label"]
        assert item["timecode_seconds"] > 0
        assert item["timecode_end_seconds"] > item["timecode_seconds"]
        assert isinstance(item["version_note"], str)
        assert "search" not in item["film_url"]
        assert "Мосфильм" not in item["film_url"]
        if item["film_url"]:
            assert urlsplit(item["film_url"]).netloc.lower() in {"vkvideo.ru", "rutube.ru"}
            assert item["film_link_kind"] == "direct"
            assert item["film_provider"] in {"VK Видео", "Rutube"}
        else:
            assert item["film_link_kind"] == "empty"
            assert item["film_provider"] == ""

    alias = client.get("/api/admin/moderation/love-confession", headers=HEADERS)
    assert alias.status_code == 200
    assert alias.json()["items"] == table["items"]

    conn = connect(config.DB_PATH)
    board = build_board(conn)
    verified_rows = conn.execute(
        f"SELECT verified FROM scenes WHERE id IN ({','.join('?' * len(table['items']))})",
        tuple(i["scene_id"] for i in table["items"]),
    ).fetchall()
    conn.close()
    assert all(row["verified"] == 0 for row in verified_rows)
    assert all(i["candidate_id"] not in candidate_ids for i in board["items"])
    assert all(i["proposal_kind"] != "love_batch" for i in board["items"])


def test_love_table_edit_saves_without_verifying_and_confirm_requires_version(client, inserted_batch):
    selected, candidate_ids = inserted_batch
    item = client.get("/api/admin/moderation/love-confession/table", headers=HEADERS).json()["items"][0]
    cid, scene_id = item["candidate_id"], item["scene_id"]
    edit_path = f"/api/admin/moderation/love-confession/{cid}/edit"

    bad = client.post(edit_path, headers=HEADERS, json={
        "timecode_label": "0:10:00", "timecode_end_label": "0:10:35",
        "film_url": "https://vkvideo.ru/search?query=amelie", "version_note": ""})
    assert bad.status_code == 400
    assert "прямая ссылка" in bad.text

    bad_range = client.post(edit_path, headers=HEADERS, json={
        "timecode_label": "0:10:00", "timecode_end_label": "0:09:00"})
    assert bad_range.status_code == 400

    saved = client.post(edit_path, headers=HEADERS, json={
        "timecode_label": "0:10:00", "timecode_end_label": "0:10:35",
        "film_url": "https://rutube.ru/video/abc/?t=999",
        "version_note": "реставрация, Rutube"})
    assert saved.status_code == 200
    assert saved.json()["verified"] == 0
    assert saved.json()["film_url"] == "https://rutube.ru/video/abc/"

    after = client.get("/api/admin/moderation/love-confession/table", headers=HEADERS).json()
    row = next(i for i in after["items"] if i["candidate_id"] == cid)
    assert row["timecode_label"] == "0:10:00"
    assert row["timecode_end_label"] == "0:10:35"
    assert row["film_url"] == "https://rutube.ru/video/abc/"
    assert row["version_note"] == "реставрация, Rutube"
    assert row["status"] == "pending" and row["verified"] is False

    conn = connect(config.DB_PATH)
    assert conn.execute("SELECT verified FROM scenes WHERE id=?", (scene_id,)).fetchone()[0] == 0
    conn.close()

    no_note = client.post("/api/admin/moderation/confirm", headers=HEADERS, json={
        "scene_id": scene_id, "action": "confirm", "candidate_id": cid,
        "timecode_label": "0:10:00", "timecode_end_label": "0:10:35"})
    assert no_note.status_code == 400
    assert "version_note" in no_note.text

    confirmed = client.post("/api/admin/moderation/confirm", headers=HEADERS, json={
        "scene_id": scene_id, "action": "confirm", "candidate_id": cid,
        "timecode_label": "0:10:00", "timecode_end_label": "0:10:35",
        "version_note": "реставрация, Rutube",
        "watch_url": "https://rutube.ru/video/abc/?t=600"})
    assert confirmed.status_code == 200
    assert confirmed.json()["verified"] == 1
    conn = connect(config.DB_PATH)
    scene = conn.execute("SELECT verified, watch_url, version_note FROM scenes WHERE id=?",
                         (scene_id,)).fetchone()
    conn.close()
    assert scene["verified"] == 1
    assert "t=600" in scene["watch_url"]
    assert scene["version_note"] == "реставрация, Rutube"

    final = client.get("/api/admin/moderation/love-confession/table", headers=HEADERS).json()
    row = next(i for i in final["items"] if i["candidate_id"] == cid)
    assert row["status"] == "verified" and row["verified"] is True
    assert final["stats"]["verified"] == 1
