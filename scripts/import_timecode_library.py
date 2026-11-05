#!/usr/bin/env python3
"""Импорт КАНДИДАТОВ таймкодов из большой библиотеки субтитров в очередь ручной сверки.

По умолчанию работает с `data/timecode_library/full_manifest.json` (62 сцены /
105 SRT+CSV из 4 GitHub-зеркал) и полным справочником `imdb_map_full.json`
(133 IMDb ID на 140 сцен каталога). `--pilot` переключает на кураторский
20-фильмовый `pilot_manifest.json`. Для OPUS / OpenSubtitles v2024 на Mac есть
отдельный `scripts/import_opus_timecodes.py`.

Скрипт:

1. скачивает SRT/CSV с `raw.githubusercontent.com` (без лимита REST API), а при недоступности raw-хоста — через `api.github.com` с `GITHUB_TOKEN`/`GH_TOKEN`, в gitignored-кэш `data/timecode_library/cache/`;
2. CSV из `jblinder/subtext` конвертирует во временный SRT (startTime/endTime в мс);
3. строит локальный манифест с путями к кэшу;
4. зовёт `scripts/fetch_timecodes.py`, который извлекает ТОЛЬКО факт времени
   (start/end + sha1 + source/license + confidence/consensus), но НЕ текст реплик;
5. проставляет `imdb_id`/`tmdb_id` из полного справочника;
6. оставляет все кандидаты в `status='pending'` — `verified=1` ставится ТОЛЬКО
   вручную в админке `#/admin` после просмотра момента в легальном плеере.

Запуск:
    .venv/bin/python scripts/import_timecode_library.py
    .venv/bin/python scripts/import_timecode_library.py --dry-run
    .venv/bin/python scripts/import_timecode_library.py --pilot
    .venv/bin/python scripts/import_timecode_library.py --db /tmp/scena.db --refresh
    .venv/bin/python scripts/import_timecode_library.py --max-per-scene 0  # все кандидаты
"""
import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config  # noqa: E402
from app.db import connect, init_db  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(BASE_DIR, "data", "timecode_library")
DEFAULT_FULL_MANIFEST = os.path.join(LIB_DIR, "full_manifest.json")
DEFAULT_PILOT_MANIFEST = os.path.join(LIB_DIR, "pilot_manifest.json")
DEFAULT_FULL_IDS = os.path.join(LIB_DIR, "imdb_map_full.json")
DEFAULT_PILOT_IDS = os.path.join(LIB_DIR, "imdb_map.json")
DEFAULT_MANIFEST = DEFAULT_FULL_MANIFEST
DEFAULT_IDS = DEFAULT_FULL_IDS
DEFAULT_CACHE = os.path.join(LIB_DIR, "cache")
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


def github_blob_to_raw(url: str) -> str:
    """https://github.com/<o>/<r>/blob/<sha>/<path> -> raw.githubusercontent.com.

    raw.githubusercontent.com НЕ расходует лимит GitHub REST API (60/час без
    токена), поэтому для публичных SRT это основной канал; api.github.com
    остаётся fallback для сетей, где raw-хост закрыт.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc not in ("github.com", "www.github.com"):
        raise ValueError(f"Ожидалась ссылка на github.com: {url}")
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 5 or parts[2] != "blob":
        raise ValueError(f"Не распознана ссылка на blob: {url}")
    owner, repo, _, sha = parts[:4]
    raw_path = "/".join(parts[4:])
    path = urllib.parse.quote(urllib.parse.unquote(raw_path), safe="/")
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{path}"


def cache_name(url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    tail = os.path.basename(urllib.parse.urlparse(url).path)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in tail)
    return f"{digest}_{safe}"


def _fmt_srt_time(ms: float) -> str:
    total = max(0, int(round(ms)))
    h, rem = divmod(total, 3600000)
    m, rem = divmod(rem, 60000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def csv_to_srt(csv_path: str, srt_path: str) -> str:
    """Конвертирует subtext-CSV (startTime/endTime в мс) во временный SRT для parse_srt()."""
    if os.path.exists(srt_path) and os.path.getsize(srt_path) > 0:
        return srt_path
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as src, \
            open(srt_path, "w", encoding="utf-8") as dst:
        reader = csv.DictReader(src)
        n = 0
        for row in reader:
            try:
                start = float(row.get("startTime") or 0)
                end = float(row.get("endTime") or start)
            except (TypeError, ValueError):
                continue
            text = (row.get("text") or "").strip()
            if not text or end <= start:
                continue
            n += 1
            dst.write(f"{n}\n{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n{text}\n\n")
    return srt_path


def download_srt(url: str, dest: str, refresh: bool = False) -> str:
    """Скачивает SRT по GitHub blob URL в dest. Возвращает путь к файлу.

    Порядок: raw.githubusercontent.com (без квоты REST API) → api.github.com с
    GITHUB_TOKEN/GH_TOKEN → api.github.com без токена. Так 105 файлов не упираются
    в анонимный лимит 60 запросов/час, а песочница с закрытым raw-хостом всё ещё
    работает через API.
    """
    if os.path.exists(dest) and not refresh and os.path.getsize(dest) > 0:
        return dest
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    ua = {"User-Agent": "scena-timecode-importer"}
    candidates: list[tuple[str, str, dict[str, str]]] = [
        ("raw", github_blob_to_raw(url), {**ua, "Accept": "text/plain"}),
    ]
    api_headers = {**ua, "Accept": "application/vnd.github.raw"}
    if token:
        candidates.append(("api+token", github_blob_to_api(url),
                           {**api_headers, "Authorization": f"Bearer {token}"}))
    candidates.append(("api", github_blob_to_api(url), api_headers))

    errors: list[str] = []
    data = b""
    for label, fetch_url, headers in candidates:
        try:
            req = urllib.request.Request(fetch_url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as response:
                data = response.read()
            if data and len(data) >= 20:
                break
            errors.append(f"{label}: пустой или слишком короткий ответ")
            data = b""
        except urllib.error.HTTPError as exc:
            hint = ""
            if exc.code == 403 and "api.github.com" in fetch_url:
                reset = exc.headers.get("X-RateLimit-Reset")
                if reset and str(reset).isdigit():
                    minutes = max(0, int((int(reset) - time.time()) / 60))
                    hint = f" Лимит GitHub API исчерпан, сброс примерно через {minutes} мин."
                else:
                    hint = " Лимит GitHub API исчерпан."
                hint += (" Задай GITHUB_TOKEN/GH_TOKEN и повтори: "
                         "export GITHUB_TOKEN=$(gh auth token) — если GitHub CLI уже залогинен.")
            errors.append(f"{label}: HTTP {exc.code}.{hint}")
        except (urllib.error.URLError, OSError) as exc:
            errors.append(f"{label}: {exc}")
    else:
        raise RuntimeError(
            f"Не удалось скачать {url}.\n" + "\n".join("  " + e for e in errors) +
            "\nПроверь сеть. Для api.github.com при 403 поможет GITHUB_TOKEN/GH_TOKEN "
            "(не присылай токен в чат, задай его только в терминале)."
        )
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
        formats = entry.get("formats") or [("csv" if f.lower().split("?")[0].endswith(".csv") else "srt") for f in files]
        if not (len(sources) == len(files) == len(formats)):
            raise ValueError(f"Разная длина file/sources/formats у {entry.get('movie_title')}")
        local_files, local_sources = [], []
        for url, src, fmt in zip(files, sources, formats):
            if not url.startswith("https://github.com/"):
                # уже локальный путь (например, при повторном запуске)
                local_files.append(url)
                local_sources.append(src)
                continue
            dest = os.path.join(cache_dir, cache_name(url))
            download_srt(url, dest, refresh=refresh)
            if fmt == "csv" or dest.lower().endswith(".csv"):
                srt = dest[:-4] + ".srt" if dest.lower().endswith(".csv") else dest + ".srt"
                dest = csv_to_srt(dest, srt)
            local_files.append(dest)
            local_sources.append(src)
        item = dict(entry)
        item.pop("formats", None)
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
    for key, meta in scenes.items():
        if not isinstance(meta, dict):
            continue
        # Полный справочник использует ключи «название|год» (в каталоге есть
        # одинаковые названия с разными годами), пилотный — просто «название».
        if "|" in key:
            title, raw_year = key.rsplit("|", 1)
            try:
                year = int(raw_year)
            except ValueError:
                year = meta.get("movie_year")
        else:
            title, year = key, meta.get("movie_year")
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
    ap.add_argument("--manifest", default=None, help="путь к manifest (по умолчанию полный каталог)")
    ap.add_argument("--ids", default=None, help="путь к IMDb/TMDB-справочнику")
    ap.add_argument("--pilot", action="store_true", help="использовать только 20-фильмовый пилот")
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE)
    ap.add_argument("--db", default=config.DB_PATH)
    ap.add_argument("--dry-run", action="store_true", help="ничего не писать в БД, только показать кандидатов")
    ap.add_argument("--refresh", action="store_true", help="перескачать SRT даже если кэш есть")
    ap.add_argument("--auto-threshold", type=float, default=0.45)
    ap.add_argument("--min-consensus", type=int, default=2)
    ap.add_argument("--max-per-scene", type=int, default=3,
                    help="сколько лучших кандидатов оставить на сцену (0 = все; для полного каталога рекомендуется 3)")
    args = ap.parse_args()

    manifest_path = args.manifest or (DEFAULT_PILOT_MANIFEST if args.pilot else DEFAULT_MANIFEST)
    ids_path = args.ids or (DEFAULT_PILOT_IDS if args.pilot else DEFAULT_IDS)
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    os.makedirs(args.cache_dir, exist_ok=True)

    print(f"== Манифест: {manifest_path}")
    print(f"== Кэш SRT: {args.cache_dir}")
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    print("== Загрузка: raw.githubusercontent.com → api.github.com" +
          (" (токен найден)" if token else " (токена нет; при 403 задай GITHUB_TOKEN)"))
    local = build_local_manifest(manifest, args.cache_dir, refresh=args.refresh)
    local_manifest_path = os.path.join(args.cache_dir, "_local_manifest.json")
    with open(local_manifest_path, "w", encoding="utf-8") as f:
        json.dump(local, f, ensure_ascii=False, indent=2)

    if not args.dry_run:
        init_db(args.db)
        conn = connect(args.db)
        n = apply_imdb_map(conn, ids_path)
        conn.close()
        print(f"== IMDb/TMDB ID проставлено/обновлено: {n} ({ids_path})")

    cmd = [
        sys.executable, FETCH_SCRIPT,
        "--manifest", local_manifest_path,
        "--db", args.db,
        "--auto-threshold", str(args.auto_threshold),
        "--min-consensus", str(args.min_consensus),
        "--max-per-scene", str(args.max_per_scene),
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
