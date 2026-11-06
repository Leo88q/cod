"""Тесты легальных deep links для ручной модерации: только VK + Rutube."""
import datetime
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import connect
from app.main import app
from app.moderation_links import (
    build_moderation_links, local_commands, moderation_coverage,
    normalize_watch_url, rutube_deep_link, tc_to_seconds, vk_deep_link,
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_tc_to_seconds_and_vk_rutube_deep_links():
    assert tc_to_seconds("0:32:56") == 1976
    assert tc_to_seconds("1:02:03") == 3723
    assert tc_to_seconds("плохо") is None
    assert vk_deep_link("227210256_456239068", 1976) == \
        "https://vkvideo.ru/video-227210256_456239068?t=1976s"
    assert vk_deep_link("-227210256_456239068", 1976) == \
        "https://vkvideo.ru/video-227210256_456239068?t=1976s"
    assert rutube_deep_link("2d0899d6388926b7caa66a09e9073572", 1976) == \
        "https://rutube.ru/video/2d0899d6388926b7caa66a09e9073572/?t=1976"


def test_normalize_watch_url_adds_time_only_to_vk_and_rutube():
    assert normalize_watch_url("https://vkvideo.ru/video-227210256_456239068", 1976) == \
        "https://vkvideo.ru/video-227210256_456239068?t=1976s"
    assert normalize_watch_url("https://vk.com/video-227210256_456239068?list=abc", 1976) == \
        "https://vk.com/video-227210256_456239068?list=abc&t=1976s"
    assert normalize_watch_url("https://rutube.ru/video/abc/?p=xyz", 1976) == \
        "https://rutube.ru/video/abc/?p=xyz&t=1976"
    assert normalize_watch_url("https://www.youtube.com/watch?v=abc", 1976) is None
    assert normalize_watch_url("https://okko.tv/movie", 1976) is None


def test_local_commands_work_without_subscription():
    cmds = local_commands(3723, "1:02:03")
    assert len(cmds) == 3
    assert "mpv --start=1:02:03" in cmds[0]["command"]
    assert "vlc --start-time=3723" in cmds[1]["command"]
    assert "IINA" in cmds[2]["app"]
    assert local_commands(None, None) == []


def _hosts(links):
    return {urlsplit(l["url"]).netloc.lower() for l in links if l.get("url")}


def test_vk_is_first_rutube_deep_is_second_and_no_other_sources():
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
    assert links[0]["provider"] == "vk_mosfilm"
    assert "vkvideo.ru" in links[0]["url"]
    official = [l for l in links if l["kind"] == "official_deep"]
    assert official and official[0]["provider"] == "rutube_mosfilm"
    assert "rutube.ru" in official[0]["url"] and "?t=1976" in official[0]["url"]
    assert official[0]["requires_version_check"] is True
    assert "version_note" in official[0]["note"]
    assert any(l["kind"] == "local" for l in links)
    assert not any("youtube.com" in l.get("url", "") or "kinopoisk" in l.get("url", "")
                   or "imdb.com" in l.get("url", "") for l in links)
    assert all(h in {"vkvideo.ru", "rutube.ru"} for h in _hosts(links))


def test_unknown_film_gets_two_searches_and_local_command_only():
    c = {"movie_title": "Неизвестный фильм", "movie_year": 2026,
         "timecode_label": "0:10:00", "timecode_seconds": 600}
    links = build_moderation_links(c)
    assert not any(l["kind"] == "official_deep" for l in links)
    searches = [l for l in links if l["kind"] == "search"]
    assert len(searches) == 2
    assert {s["provider"] for s in searches} == {"vk_mosfilm", "rutube_mosfilm"}
    assert any(l["kind"] == "local" for l in links)
    assert _hosts(links) == {"vkvideo.ru", "rutube.ru"}


def test_moderation_coverage_counts_curated_legal_videos():
    cov = moderation_coverage()
    assert cov["official_videos"] == 10
    assert cov["scenes_with_official_deep"] == 10
    assert cov["requires_version_check"] is True
    assert cov["verified_by_human"] is False
    assert set(cov["sources"]) == {"vk_mosfilm", "rutube_mosfilm"}
    assert "vkvideo.ru" in cov["vk_search"] and "rutube.ru" in cov["rutube_search"]


def test_admin_timecodes_returns_only_vk_and_rutube_links(client):
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
        (sid, "vkvideo", "0:32:56", 1976, "moderation-links-test", "test-lic",
         "m" * 40, 1, 1.2, 2, 1, now),
    )
    conn.commit()
    conn.close()
    r = client.get("/api/admin/timecodes?status=pending", headers={"X-Admin-Token": "test-admin"})
    assert r.status_code == 200
    cand = next(c for c in r.json()["candidates"] if c["scene_id"] == sid and c["source"] == "moderation-links-test")
    links = cand["moderation_links"]
    assert any(l["kind"] == "official_deep" and "rutube.ru" in l["url"] and "?t=1976" in l["url"] for l in links)
    assert any(l["kind"] == "search" and "vkvideo.ru" in l["url"] for l in links)
    assert any(l["kind"] == "local" for l in links)
    assert all(h in {"vkvideo.ru", "rutube.ru"} for h in _hosts(links))


def test_library_endpoint_exposes_rutube_manual_moderation_targets(client):
    r = client.get("/api/admin/timecode-library", headers={"X-Admin-Token": "test-admin"})
    assert r.status_code == 200
    targets = r.json()["moderation"]["targets"]
    assert len(targets) >= 8
    assert any(t["movie_title"] == "Ирония судьбы, или С лёгким паром!"
               and t["has_deep_link"] and "rutube.ru" in t["url"] for t in targets)
    assert all("movie_title" in t and "movie_year" in t and "scene_id" in t for t in targets)


def test_manual_candidate_is_pending_and_requires_version_note(client):
    payload = {
        "movie_title": "Белое солнце пустыни",
        "movie_year": 1969,
        "timecode_label": "0:32:56",
        "timecode_end_label": "0:33:21",
        "moment_label": "Женя у двери",
        "version_note": "Мосфильм, реставрация, Rutube",
        "source_url": "https://rutube.ru/video/e7273eab7e8c43196ce17c80f1bd4dd5/?t=1976",
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
        " FROM timecode_candidates tc JOIN scenes s ON s.id=tc.scene_id WHERE tc.id=?", (cid,),
    ).fetchone()
    conn.close()
    assert row["status"] == "pending"
    assert row["timecode_seconds"] == 1976 and row["timecode_end_seconds"] == 2001
    assert row["verified"] == 0, "ручной кандидат НЕ должен сразу становиться verified"
    assert "rutube.ru" in row["source"]
