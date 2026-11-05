"""Тесты моста «большая библиотека SRT -> очередь ручной сверки»."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import import_timecode_library as lib  # noqa: E402

from app.db import connect, init_db  # noqa: E402


def test_github_blob_to_api_encodes_path_once():
    url = ("https://github.com/mesutgurlek/Movie-Category-Classification-from-Subtitles/blob/"
           "7ebe351e15ff8220cb8432747902002e5904e349/NonImpairedSubtitles/Comedy/Forrest%20Gump.srt")
    api = lib.github_blob_to_api(url)
    assert api.startswith("https://api.github.com/repos/mesutgurlek/")
    assert "Forrest%20Gump.srt" in api
    assert "%2520" not in api, "не должно быть двойного percent-encoding"
    assert "ref=7ebe351e15ff8220cb8432747902002e5904e349" in api


def test_build_local_manifest_uses_cache(monkeypatch, tmp_path):
    calls = []

    def fake_download(url, dest, refresh=False):
        calls.append((url, dest, refresh))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write("1\n00:00:01,000 --> 00:00:02,000\nx\n")
        return dest

    monkeypatch.setattr(lib, "download_srt", fake_download)
    manifest = {"entries": [{
        "movie_title": "Forrest Gump", "movie_year": 1994, "lang": "en",
        "service": "kinopoisk", "source_license": "test-license",
        "file": ["https://github.com/o/r/blob/sha/Forrest%20Gump.srt"],
        "sources": ["https://github.com/o/r/blob/sha/Forrest%20Gump.srt"],
        "scenes": [{"match_title": "Форрест Гамп", "keywords": ["run"], "moment_label": "момент бега"}],
    }]}
    out = lib.build_local_manifest(manifest, str(tmp_path))
    assert len(calls) == 1
    local_file = out["entries"][0]["file"][0]
    assert local_file.startswith(str(tmp_path))
    assert os.path.exists(local_file)
    assert out["entries"][0]["sources"] == manifest["entries"][0]["sources"]
    assert out["entries"][0]["scenes"][0]["moment_label"] == "момент бега"


def test_apply_imdb_map_and_placeholder_cleanup(tmp_path):
    db = str(tmp_path / "scena.db")
    init_db(db)
    conn = connect(db)
    # сид мог оставить заглушку; init_db должен её убрать
    conn.execute("UPDATE scenes SET timecode_label='0:00:00', timecode_seconds=NULL, verified=0 WHERE id=1")
    conn.commit()
    init_db(db)  # повторная инициализация чистит заглушку
    conn = connect(db)
    row = conn.execute("SELECT timecode_label, timecode_seconds FROM scenes WHERE id=1").fetchone()
    assert row["timecode_label"] is None
    assert row["timecode_seconds"] is None

    ids = str(tmp_path / "ids.json")
    with open(ids, "w", encoding="utf-8") as f:
        json.dump({"scenes": {"Ирония судьбы, или С лёгким паром!": {
            "original_title": "Irony of Fate", "movie_year": 1975,
            "imdb_id": "tt0064481", "tmdb_id": None}}}, f, ensure_ascii=False)
    changed = lib.apply_imdb_map(conn, ids)
    assert changed >= 1
    got = conn.execute("SELECT imdb_id FROM scenes WHERE movie_title='Ирония судьбы, или С лёгким паром!' AND movie_year=1975").fetchone()
    assert got["imdb_id"] == "tt0064481"
    conn.close()
