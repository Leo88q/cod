"""Тесты легальных deep links для ручной модерации таймкодов."""
import datetime
import os

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import connect
from app.main import app
from app.moderation_links import (
    build_moderation_links, local_commands, moderation_coverage,
    normalize_watch_url, tc_to_seconds, youtube_deep_link,
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_tc_to_seconds_and_youtube_deep_link():
    assert tc_to_seconds("0:32:56") == 1976
    assert tc_to_seconds("1:02:03") == 3723
    assert tc_to_seconds("плохо") is None
    assert youtube_deep_link("y5CQr_D-NZg", 1976) == \
        "https://www.youtube.com/watch?v=y5CQr_D-NZg&t=1976s"


def test_normalize_watch_url_adds_time_only_where_supported():
    assert normalize_watch_url("https://www.youtube.com/watch?v=abc", 1976) == \
        "https://www.youtube.com/watch?v=abc&t=1976s"
    assert normalize_watch_url("https://www.youtube.com/watch?v=abc&t=1s", 1976) == \
        "https://www.youtube.com/watch?v=abc&t=1976s"
    assert normalize_watch_url("https://youtu.be/abc", 1976) == \
        "https://www.youtube.com/watch?v=abc&t=1976s"
    assert normalize_watch_url("https://vimeo.com/123", 1976).endswith("#t=1976s")
    assert normalize_watch_url("https://okko.tv/movie", 1976) is None


def test_local_commands_work_without_subscription():
    cmds = local_commands(3723, "1:02:03")
    assert len(cmds) == 3
    assert "mpv --start=1:02:03" in cmds[0]["command"]
    assert "vlc --start-time=3723" in cmds[1]["command"]
    assert "IINA" in cmds[2]["app"]
    assert local_commands(None, None) == []


def test_official_mosfilm_deep_link_is_first_and_requires_version_check():
    c = {
        "movie_title": "Ирония судьбы, или С лёгким паром!",
        "movie_year": 1975,
        "imdb_id": "tt0073179",
        "timecode_label": "0:32:56",
        "timecode_seconds": 1976,
        "timecode_end_label": "0:33:21",
        "timecode_end_seconds": 2001,
    }
    links = build_moderation_links(c)
    official = [l for l in links if l["kind"] == "official_deep"]
    assert official, "должен найтись официальный бесплатный full-movie deep link"
    assert official[0]["priority"] == 1
    assert "y5CQr_D-NZg" in official[0]["url"]
    assert "t=1976s" in official[0]["url"]
    assert official[0]["requires_version_check"] is True
    assert "version_note" in official[0]["note"]
    assert any(l["kind"] == "local" for l in links)
    assert any(l["kind"] == "search" and l["label"].endswith("kinopoisk") for l in links)
    assert any(l["kind"] == "identify" and "imdb.com" in l["url"] for l in links)
    # приоритет: official_deep раньше платного поиска
    assert links.index(official[0]) < min(i for i, l in enumerate(links) if l["kind"] == "search")


def test_unknown_film_still_gets_local_and_search_fallbacks():
    c = {"movie_title": "Неизвестный фильм", "movie_year": 2026,
         "timecode_label": "0:10:00", "timecode_seconds": 600}
    links = build_moderation_links(c)
    assert not any(l["kind"] == "official_deep" for l in links)
    assert any(l["kind"] == "local" for l in links)
    assert any(l["kind"] == "search" for l in links)
    assert any(l["kind"] == "official_search" for l in links)


def test_moderation_coverage_counts_curated_legal_videos():
    cov = moderation_coverage()
    assert cov["official_videos"] >= 10
    assert cov["scenes_with_official_deep"] >= 8
    assert cov["requires_version_check"] is True
    assert cov["verified_by_human"] is False
    assert "mosfilm_youtube" in cov["sources"]


def test_admin_timecodes_returns_moderation_links(client):
    conn = connect(config.DB_PATH)
    row = conn.execute(
        "SELECT id FROM scenes WHERE movie_title='Ирония судьбы, или С лёгким паром!' AND movie_year=1975"
    ).fetchone()
    assert row, "сцена каталога должна существовать"
    sid = row["id"]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO timecode_candidates (scene_id, service, timecode_label, timecode_seconds,"
        " source, source_license, evidence_sha1, line_index, confidence, consensus, auto,"
        " status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',?)",
        (sid, "kinopoisk", "0:32:56", 1976, "moderation-links-test", "test-lic",
         "m" * 40, 1, 1.2, 2, 1, now),
    )
    conn.commit()
    conn.close()
    r = client.get("/api/admin/timecodes?status=pending", headers={"X-Admin-Token": "test-admin"})
    assert r.status_code == 200
    cand = next(c for c in r.json()["candidates"] if c["scene_id"] == sid and c["source"] == "moderation-links-test")
    links = cand["moderation_links"]
    assert any(l["kind"] == "official_deep" and "t=1976s" in l["url"] for l in links)
    assert any(l["kind"] == "local" for l in links)


def test_library_endpoint_exposes_manual_moderation_targets(client):
    r = client.get("/api/admin/timecode-library", headers={"X-Admin-Token": "test-admin"})
    assert r.status_code == 200
    targets = r.json()["moderation"]["targets"]
    assert len(targets) >= 8
    assert any(t["movie_title"] == "Ирония судьбы, или С лёгким паром!" and t["has_deep_link"] for t in targets)
    assert all("movie_title" in t and "movie_year" in t and "scene_id" in t for t in targets)


def test_manual_candidate_is_pending_and_requires_version_note(client):
    payload = {
        "movie_title": "Ирония судьбы, или С лёгким паром!",
        "movie_year": 1975,
        "timecode_label": "0:32:56",
        "timecode_end_label": "0:33:21",
        "moment_label": "Женя у двери",
        "version_note": "Мосфильм, реставрация, YouTube",
        "source_url": "https://www.youtube.com/watch?v=y5CQr_D-NZg",
    }
    bad = dict(payload, version_note="")
    r = client.post("/api/admin/timecodes/manual", headers={"X-Admin-Token": "test-admin"}, json=bad)
    assert r.status_code == 400
    bad_tc = dict(payload, timecode_label="не время")
    r = client.post("/api/admin/timecodes/manual", headers={"X-Admin-Token": "test-admin"}, json=bad_tc)
    assert r.status_code == 400
    r = client.post("/api/admin/timecodes/manual", headers={"X-Admin-Token": "test-admin"}, json=payload)
    assert r.status_code == 200 and r.json()["status"] == "pending"
    cid = r.json()["id"]
    conn = connect(config.DB_PATH)
    row = conn.execute(
        "SELECT tc.status, tc.timecode_seconds, tc.timecode_end_seconds, tc.source, s.verified"
        " FROM timecode_candidates tc JOIN scenes s ON s.id=tc.scene_id WHERE tc.id=?", (cid,)
    ).fetchone()
    conn.close()
    assert row["status"] == "pending"
    assert row["timecode_seconds"] == 1976 and row["timecode_end_seconds"] == 2001
    assert row["verified"] == 0, "ручной кандидат НЕ должен сразу становиться verified"
    assert "youtube.com" in row["source"]
