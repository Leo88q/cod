#!/usr/bin/env python3
"""Импорт КАНДИДАТОВ таймкодов из большой библиотеки субтитров в очередь ручной сверки.

Источник: `data/timecode_library/pilot_manifest.json` — точные ссылки на SRT-зеркала
OpenSubtitles на GitHub. Скрипт:

1. скачивает SRT через `api.github.com` в gitignored-кэш `data/timecode_library/cache/`;
2. строит локальный манифест с путями к кэшу;
3. зовёт `scripts/fetch_timecodes.py`, который извлекает ТОЛЬКО факт времени
   (start/end + sha1 + source/license + confidence/consensus), но НЕ текст реплик;
4. проставляет `imdb_id` для пилотных сцен из `data/timecode_library/imdb_map.json`;
5. оставляет все кандидаты в `status='pending'` — `verified=1` ставится ТОЛЬКО
   вручную в админке `#/admin` после просмотра момента в легальном плеере.

Запуск:
    .venv/bin/python scripts/import_timecode_library.py
    .venv/bin/python scripts/import_timecode_library.py --dry-run
    .venv/bin/python scripts/import_timecode_library.py --db /tmp/scena.db --refresh
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config  # noqa: E402
from app.db import connect, init_db  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MANIFEST = os.path.join(BASE_DIR, "data", "timecode_library", "pilot_manifest.json")
DEFAULT_IDS = os.path.join(BASE_DIR, "data", "timecode_library", "imdb_map.json")
DEFAULT_CACHE = os.path.join(BASE_DIR, "data", "timecode_library", "cache")
FETCH_SCRIPT = os.path.join(BASE_DIR, "scripts", "fetch_timecodes.py")


def github_blob_to_api(url: str) -> str:
    """https://github.com/<o>/<r>/blob/<sha>/<path> -> api.github.com raw-content URL."""
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc not in ("github.com", "www.github.com"):
        raise ValueError(f"Ожидалась ссылка на github.com: {url}")
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 5 or parts[2] != "blob":
        raise ValueError(f"Не распознана ссылка на blob: {url}")
    owner, repo, _, sha = parts[:4]
    raw_path = "/".join(parts[4:])
    # Путь уже может быть процент-закодирован; нормализуем, чтобы не получить двойное кодирование.
    path = urllib.parse.quote(urllib.parse.unquote(raw_path), safe="/")
    return f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={sha}"


def cache_name(url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    tail = os.path.basename(urllib.parse.urlparse(url).path)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in tail)
    return f"{digest}_{safe}"


def download_srt(url: str, dest: str, refresh: bool = False) -> str:
    """Скачивает SRT по GitHub blob URL в dest. Возвращает путь к файлу."""
    if os.path.exists(dest) and not refresh and os.path.getsize(dest) > 0:
        return dest
    api_url = github_blob_to_api(url)
    headers = {
        "Accept": "application/vnd.github.raw",
        "User-Agent": "scena-timecode-importer",
    }
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(api_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Не удалось скачать {url}: HTTP {exc.code}. "
            "Проверь сеть/лимит GitHub API; при 403 поможет GITHUB_TOKEN."
        ) from exc
    if not data or len(data) < 20:
        raise RuntimeError(f"Пустой или подозрительно маленький SRT: {url}")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def build_local_manifest(manifest: dict, cache_dir: str, refresh: bool = False) -> dict:
    """Заменяет удалённые `file`/`sources` на пути к локальному кэшу."""
    entries = manifest["entries"] if isinstance(manifest, dict) else manifest
    local = []
    for entry in entries:
        files = entry["file"] if isinstance(entry["file"], list) else [entry["file"]]
        sources = entry.get("sources") or [entry.get("source", "local")] * len(files)
        if len(sources) != len(files):
            raise ValueError(f"Разная длина file/sources у {entry.get('movie_title')}")
        local_files, local_sources = [], []
        for url, src in zip(files, sources):
            if not url.startswith("https://github.com/"):
                # уже локальный путь (например, при повторном запуске)
                local_files.append(url)
                local_sources.append(src)
                continue
            dest = os.path.join(cache_dir, cache_name(url))
            download_srt(url, dest, refresh=refresh)
            local_files.append(dest)
            local_sources.append(src)
        item = dict(entry)
        item["file"] = local_files if isinstance(entry["file"], list) else local_files[0]
        item["sources"] = local_sources
        local.append(item)
    return {"entries": local}


def apply_imdb_map(conn, ids_path: str) -> int:
    """Проставляет imdb_id/tmdb_id у сцен, где они ещё пустые. Не трогает проверенные таймкоды."""
    if not os.path.exists(ids_path):
        return 0
    with open(ids_path, encoding="utf-8") as f:
        data = json.load(f)
    scenes = data.get("scenes", data)
    updated = 0
    for title, meta in scenes.items():
        if not isinstance(meta, dict):
            continue
        year = meta.get("movie_year")
        imdb = meta.get("imdb_id")
        tmdb = meta.get("tmdb_id")
        if not year or (not imdb and not tmdb):
            continue
        cur = conn.execute(
            "UPDATE scenes SET imdb_id=COALESCE(NULLIF(imdb_id,''), ?), "
            " tmdb_id=COALESCE(NULLIF(tmdb_id,''), ?) "
            " WHERE movie_title=? AND movie_year=? AND (imdb_id IS NULL OR imdb_id='' OR tmdb_id IS NULL OR tmdb_id='')",
            (imdb, tmdb, title, year),
        )
        updated += cur.rowcount
    conn.commit()
    return updated


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--ids", default=DEFAULT_IDS)
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE)
    ap.add_argument("--db", default=config.DB_PATH)
    ap.add_argument("--dry-run", action="store_true", help="ничего не писать в БД, только показать кандидатов")
    ap.add_argument("--refresh", action="store_true", help="перескачать SRT даже если кэш есть")
    ap.add_argument("--auto-threshold", type=float, default=0.45)
    ap.add_argument("--min-consensus", type=int, default=2)
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    os.makedirs(args.cache_dir, exist_ok=True)

    print(f"== Кэш SRT: {args.cache_dir}")
    local = build_local_manifest(manifest, args.cache_dir, refresh=args.refresh)
    local_manifest_path = os.path.join(args.cache_dir, "_local_manifest.json")
    with open(local_manifest_path, "w", encoding="utf-8") as f:
        json.dump(local, f, ensure_ascii=False, indent=2)

    if not args.dry_run:
        init_db(args.db)
        conn = connect(args.db)
        n = apply_imdb_map(conn, args.ids)
        conn.close()
        print(f"== IMDb/TMDB ID проставлено/обновлено: {n}")

    cmd = [
        sys.executable, FETCH_SCRIPT,
        "--manifest", local_manifest_path,
        "--db", args.db,
        "--auto-threshold", str(args.auto_threshold),
        "--min-consensus", str(args.min_consensus),
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    print("== Извлечение кандидатов таймкодов")
    subprocess.run(cmd, check=True, cwd=BASE_DIR)

    if not args.dry_run:
        conn = connect(args.db)
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM timecode_candidates GROUP BY status ORDER BY status"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM timecode_candidates").fetchone()["n"]
        with_label = conn.execute(
            "SELECT COUNT(*) AS n FROM timecode_candidates WHERE moment_label IS NOT NULL AND moment_label<>''"
        ).fetchone()["n"]
        conn.close()
        print(f"== Итого кандидатов в БД: {total}")
        for r in rows:
            print(f"   {r['status']}: {r['n']}")
        print(f"   с нашей меткой момента: {with_label}")
        print("== Дальше: открой #/admin, введи ADMIN_TOKEN и проверь каждый момент в легальном плеере.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
