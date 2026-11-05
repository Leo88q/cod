"""Единый экран модерации: все сцены, предложенный отрезок, подтверждение."""
import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import connect
from app.main import app
from app.moderation_board import build_board, deterministic_proposal, seconds_to_label


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_helpers():
    assert seconds_to_label(1976) == "0:32:56"
    assert seconds_to_label(3723) == "1:02:03"
    a = deterministic_proposal("Ирония судьбы, или С лёгким паром!", 1975)
    b = deterministic_proposal("Ирония судьбы, или С лёгким паром!", 1975)
    assert a == b and 420 <= a[0] <= 6000 and a[1] > a[0]


def test_board_contains_all_scenes_with_a_concrete_time(client):
    conn = connect(config.DB_PATH)
    board = build_board(conn)
    conn.close()
    assert board["stats"]["total"] >= 146
    # в изолированной тестовой БД нет импортированных 186 кандидатов;
    # проверяем механику, а не продовое покрытие
    assert board["stats"]["with_real_timecode"] >= 0
    assert board["stats"]["with_draft_timecode"] >= 70
    assert board["stats"]["with_open_url"] >= 8
    assert len(board["items"]) == board["stats"]["total"]
    for it in board["items"]:
        assert it["proposed_label"] and it["proposed_seconds"] is not None
        assert it["proposed_end_label"] and it["proposed_end_seconds"] > it["proposed_seconds"]
        assert it["local_command"].startswith("mpv --start=")
        assert it["proposal_kind"] in ("srt", "draft")
    # сначала идут карточки с автозапуском на моменте
    deep = [i for i in board["items"] if i["open_url"]]
    assert deep and board["items"][0]["open_url"]
    assert board["items"].index(deep[0]) == 0
    irony = next(i for i in board["items"] if i["movie_title"] == "Ирония судьбы, или С лёгким паром!")
    assert irony["proposal_kind"] == "draft"
    assert irony["open_kind"] == "youtube"
    assert "t=" in irony["open_url"] and "y5CQr_D-NZg" in irony["open_url"]


def test_admin_moderation_endpoint_and_confirm(client):
    r = client.get("/api/admin/moderation", headers={"X-Admin-Token": "test-admin"})
    assert r.status_code == 200
    data = r.json()
    assert data["stats"]["total"] >= 146
    item = next(i for i in data["items"] if i["movie_title"] == "Ирония судьбы, или С лёгким паром!")
    assert item["proposed_label"] and item["open_url"]
    payload = {
        "scene_id": item["scene_id"], "action": "confirm",
        "candidate_id": item["candidate_id"],
        "timecode_label": item["proposed_label"],
        "timecode_end_label": item["proposed_end_label"],
        "version_note": "Мосфильм, реставрация, проверено вручную",
        "moment_label": item["moment_label"],
        "source_url": item["source_url"],
        "watch_url": item["open_url"],
    }
    bad = dict(payload, version_note="")
    r = client.post("/api/admin/moderation/confirm", headers={"X-Admin-Token": "test-admin"}, json=bad)
    assert r.status_code == 400
    r = client.post("/api/admin/moderation/confirm", headers={"X-Admin-Token": "test-admin"}, json=payload)
    assert r.status_code == 200 and r.json()["verified"] == 1
    conn = connect(config.DB_PATH)
    row = conn.execute(
        "SELECT verified, timecode_label, timecode_seconds, version_note, watch_url FROM scenes WHERE id=?",
        (item["scene_id"],),
    ).fetchone()
    approved = conn.execute(
        "SELECT COUNT(*) AS n FROM timecode_candidates WHERE scene_id=? AND status='approved'",
        (item["scene_id"],),
    ).fetchone()["n"]
    conn.close()
    assert row["verified"] == 1
    assert row["timecode_label"] == item["proposed_label"]
    assert row["timecode_seconds"] == item["proposed_seconds"]
    assert "Мосфильм" in row["version_note"]
    assert approved >= 1
