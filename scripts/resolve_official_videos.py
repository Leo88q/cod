#!/usr/bin/env python3
"""Помощник пополнения `data/timecode_library/official_free_videos.json` на Mac.

НЕ скачивает видео и НЕ добавляет записи автоматически. Ищет через локально
установленный `yt-dlp` официальные бесплатные загрузки и печатает кандидатов
для РУЧНОЙ проверки человеком: совпадает ли фильм, часть, издание, канал.

    python3 -m pip install yt-dlp   # только на машине оператора
    .venv/bin/python scripts/resolve_official_videos.py \
        --key "Ирония судьбы, или С лёгким паром!|1975" \
        --imdb-id tt0073179 --query "Ирония судьбы полный фильм"

Вывод — JSON-массив черновиков. Их нужно сверить с официальным каналом/страницей
и лишь потом вручную внести в `official_free_videos.json`.
"""
import argparse
import json
import shutil
import subprocess
import sys

CHANNEL = "Киноконцерн Мосфильм"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True, help="что искать, например название фильма")
    ap.add_argument("--key", default="", help="ключ каталога «Название|год»")
    ap.add_argument("--imdb-id", default="", help="IMDb ID, если известен")
    ap.add_argument("--channel", default=CHANNEL)
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    if not shutil.which("yt-dlp"):
        print("yt-dlp не найден. Установи его локально: python3 -m pip install yt-dlp", file=sys.stderr)
        return 1
    search = f"ytsearch{args.limit}:{args.channel} {args.query} полный фильм"
    proc = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings", search],
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        print(proc.stderr.strip() or "yt-dlp не вернул результатов", file=sys.stderr)
        return 1
    drafts = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = row.get("id")
        if not vid:
            continue
        drafts.append({
            "key": args.key,
            "imdb_id": args.imdb_id or None,
            "provider": "youtube_mosfilm",
            "video_id": vid,
            "template": "youtube",
            "title": row.get("title"),
            "channel": row.get("channel") or row.get("uploader"),
            "url": row.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
            "version_hint": "Черновик из поиска: проверь канал, часть, издание и длительность вручную.",
            "evidence_url": row.get("webpage_url"),
            "evidence_note": "Найдено через yt-dlp; требуется ручная проверка.",
        })
    print(json.dumps(drafts, ensure_ascii=False, indent=2))
    print("\nНе добавляй автоматически: сначала открой каждую ссылку и подтверди официальный канал.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
