#!/usr/bin/env python3
"""Помощник куратора: черновик записи для VK Видео / Rutube.

НЕ ищет видео, НЕ скачивает его и НЕ добавляет запись автоматически.
Оператор сам открывает официальный канал «Мосфильм» в VK Видео или Rutube,
копирует ссылку на полный фильм и передаёт её сюда. Скрипт только достаёт
`video_id` и печатает JSON-черновик для ручной проверки издания/серии.

    .venv/bin/python scripts/resolve_official_videos.py \\
        --url "https://rutube.ru/video/2d0899d6388926b7caa66a09e9073572/" \\
        --key "Ирония судьбы, или С лёгким паром!|1975" \\
        --imdb-id tt0073179

Вывод нужно сверить с официальным каналом и лишь потом вручную внести в
`data/timecode_library/official_free_videos.json`.
"""
import argparse
import json
import sys
from urllib.parse import urlsplit


def draft_from_url(url: str, key: str, imdb_id: str) -> dict | None:
    p = urlsplit(url.strip())
    host = (p.netloc or "").lower()
    path = p.path.strip("/")
    if "rutube.ru" in host and "/video/" in f"/{path}/":
        video_id = path.split("video/", 1)[1].strip("/").split("/", 1)[0]
        template, provider = "rutube", "rutube_mosfilm"
    elif ("vkvideo.ru" in host or "vk.com" in host or "vk.ru" in host) and "video-" in path:
        video_id = path.split("video-", 1)[1].split("/", 1)[0]
        template, provider = "vk_video", "vk_mosfilm"
    else:
        return None
    return {
        "key": key,
        "imdb_id": imdb_id or None,
        "provider": provider,
        "video_id": video_id,
        "template": template,
        "url": url.strip(),
        "version_hint": "Черновик из официальной ссылки: проверь канал, часть, издание и длительность вручную.",
        "evidence_url": url.strip(),
        "evidence_note": "Ссылка вставлена оператором; требуется ручная проверка.",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="ссылка на полный фильм в VK Видео или Rutube")
    ap.add_argument("--key", default="", help="ключ каталога «Название|год»")
    ap.add_argument("--imdb-id", default="", help="IMDb ID, если известен")
    args = ap.parse_args()
    draft = draft_from_url(args.url, args.key, args.imdb_id)
    if not draft:
        print("Нужна ссылка вида https://rutube.ru/video/<id>/ или https://vkvideo.ru/video-<owner>_<id>", file=sys.stderr)
        return 1
    print(json.dumps([draft], ensure_ascii=False, indent=2))
    print("\nНе добавляй автоматически: сначала открой ссылку и подтверди официальный канал, полный фильм и серию.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
