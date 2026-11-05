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


def test_full_manifest_and_imdb_map_are_consistent():
    lib = os.path.join(os.path.dirname(__file__), "..", "data", "timecode_library")
    full = json.load(open(os.path.join(lib, "full_manifest.json"), encoding="utf-8"))
    ids = json.load(open(os.path.join(lib, "imdb_map_full.json"), encoding="utf-8"))
    assert full["stats"]["catalog_scenes"] == 140
    assert full["stats"]["matched_scenes"] >= 60
    assert full["stats"]["subtitle_files"] >= 100
    assert full["stats"]["excluded_false_matches"] >= 1
    assert len(full["entries"]) == full["stats"]["matched_scenes"]
    assert all(e["file"] and e["sources"] and e["formats"] for e in full["entries"])
    assert all(e["scenes"][0]["moment_label"] for e in full["entries"])
    # дубликаты названий в полном справочнике различаются годом
    assert "Начало|1970" in ids["scenes"] and "Начало|2010" in ids["scenes"]
    assert ids["scenes"]["Начало|1970"]["imdb_id"] != ids["scenes"]["Начало|2010"]["imdb_id"]
    assert all(v.get("original_title") for v in ids["scenes"].values())
    assert ids["stats"]["with_imdb"] >= 130
    excluded = {x["key"] for x in full["excluded"]}
    matched_titles = {e["scenes"][0]["match_title"] + "|" + str(e["movie_year"]) for e in full["entries"]}
    assert excluded.isdisjoint(matched_titles)
    # кэш SRT gitignored и в CI может отсутствовать — тест не должен от него зависеть


def test_csv_to_srt_converts_subtext_milliseconds(tmp_path):
    csv_path = str(tmp_path / "sub.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write('"id","startTime","endTime","text","title"\n')
        f.write('"1",26120,29635,"[SIREN WAILING]","10 Rillington Place"\n')
        f.write('"2",29839,33548,"MAN: Miss Eady?","10 Rillington Place"\n')
    srt_path = str(tmp_path / "sub.srt")
    lib.csv_to_srt(csv_path, srt_path)
    text = open(srt_path, encoding="utf-8").read()
    assert "00:00:26,120 --> 00:00:29,635" in text
    assert "MAN: Miss Eady?" in text
    assert "[SIREN WAILING]" in text


def test_apply_imdb_map_supports_title_year_keys(tmp_path):
    db = str(tmp_path / "scena.db")
    init_db(db)
    conn = connect(db)
    ids = str(tmp_path / "ids.json")
    with open(ids, "w", encoding="utf-8") as f:
        json.dump({"scenes": {"Начало|1970": {
            "original_title": "The Beginning", "movie_year": 1970,
            "imdb_id": "tt0066124", "tmdb_id": None}}}, f, ensure_ascii=False)
    changed = lib.apply_imdb_map(conn, ids)
    assert changed >= 1
    row = conn.execute("SELECT imdb_id FROM scenes WHERE movie_title='Начало' AND movie_year=1970").fetchone()
    assert row["imdb_id"] == "tt0066124"
    other = conn.execute("SELECT imdb_id FROM scenes WHERE movie_title='Начало' AND movie_year=2010").fetchone()
    assert other["imdb_id"] is None or other["imdb_id"] != "tt0066124"
    conn.close()


def test_build_local_manifest_converts_csv_to_srt(monkeypatch, tmp_path):
    def fake_download(url, dest, refresh=False):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write('"id","startTime","endTime","text","title"\n')
            f.write('"1",1000,3000,"hello world","Example"\n')
        return dest

    monkeypatch.setattr(lib, "download_srt", fake_download)
    manifest = {"entries": [{
        "movie_title": "Example", "movie_year": 2001, "lang": "en",
        "service": "kinopoisk", "source_license": "test",
        "file": ["https://github.com/o/r/blob/sha/Example.csv"],
        "sources": ["https://github.com/o/r/blob/sha/Example.csv"],
        "formats": ["csv"],
        "scenes": [{"match_title": "Пример", "keywords": ["hello"], "moment_label": "момент"}],
    }]}
    out = lib.build_local_manifest(manifest, str(tmp_path))
    local = out["entries"][0]["file"][0]
    assert local.endswith(".srt")
    assert "hello world" in open(local, encoding="utf-8").read()
    assert "formats" not in out["entries"][0]


def test_github_blob_urls_raw_and_api():
    url = ("https://github.com/mesutgurlek/Movie-Category-Classification-from-Subtitles/"
           "blob/7ebe351e15ff8220cb8432747902002e5904e349/"
           "NonImpairedSubtitles/Adventure/The%20Incredibles.srt")
    assert lib.github_blob_to_raw(url) == (
        "https://raw.githubusercontent.com/mesutgurlek/"
        "Movie-Category-Classification-from-Subtitles/7ebe351e15ff8220cb8432747902002e5904e349/"
        "NonImpairedSubtitles/Adventure/The%20Incredibles.srt")
    assert lib.github_blob_to_api(url).startswith(
        "https://api.github.com/repos/mesutgurlek/Movie-Category-Classification-from-Subtitles/contents/")
    assert "ref=7ebe351e" in lib.github_blob_to_api(url)


def test_download_srt_uses_raw_first_without_api_quota(monkeypatch, tmp_path):
    import urllib.error
    calls = []

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"1\n00:00:01,000 --> 00:00:03,000\nhello world\n\n"

    def fake_urlopen(req, timeout=60):
        calls.append(req.full_url)
        assert req.full_url.startswith("https://raw.githubusercontent.com/"), \
            "первый запрос должен идти на raw, а не в REST API"
        return Resp()

    monkeypatch.setattr(lib.urllib.request, "urlopen", fake_urlopen)
    url = "https://github.com/o/r/blob/sha123/Example.srt"
    dest = str(tmp_path / "x.srt")
    assert lib.download_srt(url, dest) == dest
    assert len(calls) == 1
    assert "hello world" in open(dest, encoding="utf-8").read()


def test_download_srt_falls_back_to_api_with_token(monkeypatch, tmp_path):
    import urllib.error
    calls = []

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"1\n00:00:01,000 --> 00:00:03,000\nfallback ok\n\n"

    def fake_urlopen(req, timeout=60):
        calls.append(req.full_url)
        if req.full_url.startswith("https://raw.githubusercontent.com/"):
            raise urllib.error.URLError("raw host blocked in sandbox")
        assert req.get_header("Authorization") == "Bearer test-token"
        return Resp()

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(lib.urllib.request, "urlopen", fake_urlopen)
    dest = str(tmp_path / "x.srt")
    assert lib.download_srt("https://github.com/o/r/blob/sha123/Example.srt", dest) == dest
    assert len(calls) == 2, "должны быть raw + api+token, без анонимного api"
    assert "fallback ok" in open(dest, encoding="utf-8").read()


def test_download_srt_rate_limit_error_has_actionable_hint(monkeypatch, tmp_path):
    import urllib.error

    def fake_urlopen(req, timeout=60):
        if req.full_url.startswith("https://raw.githubusercontent.com/"):
            raise urllib.error.URLError("raw blocked")
        raise urllib.error.HTTPError(req.full_url, 403, "rate limit exceeded",
                                    {"X-RateLimit-Remaining": "0",
                                     "X-RateLimit-Reset": str(int(lib.time.time()) + 1800)}, None)

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(lib.urllib.request, "urlopen", fake_urlopen)
    try:
        lib.download_srt("https://github.com/o/r/blob/sha123/Example.srt", str(tmp_path / "x.srt"))
    except RuntimeError as exc:
        msg = str(exc)
        assert "GITHUB_TOKEN" in msg
        assert "Лимит GitHub API исчерпан" in msg
        assert "сброс примерно через" in msg
    else:
        raise AssertionError("ожидался RuntimeError с подсказкой про токен")
