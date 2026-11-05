#!/usr/bin/env python3
"""Обновляет инвентарь путей внешних SRT/CSV библиотек субтитров.

Не скачивает сами субтитры: только читает GitHub Git Trees API и пишет
`data/timecode_library/srt_index.json` + счётчики в `source_registry.json`.
Для повторного запуска нужен доступ к api.github.com; `GITHUB_TOKEN` опционален
(без него лимит анонимного API меньше, но четырёх деревьев обычно хватает).

    .venv/bin/python scripts/build_srt_index.py
    GITHUB_TOKEN=... .venv/bin/python scripts/build_srt_index.py
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(BASE_DIR, "data", "timecode_library")
REGISTRY = os.path.join(LIB_DIR, "source_registry.json")
INDEX = os.path.join(LIB_DIR, "srt_index.json")


def github_tree(repo: str, sha: str, token: str | None = None) -> list[str]:
    url = f"https://api.github.com/repos/{repo}/git/trees/{sha}?recursive=1"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "scena-srt-index"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub tree {repo}@{sha[:7]}: HTTP {exc.code}. Нужен ли GITHUB_TOKEN?") from exc
    if data.get("truncated"):
        print(f"ВНИМАНИЕ: дерево {repo} обрезано GitHub API (truncated=true)", file=sys.stderr)
    return [item["path"] for item in data.get("tree", []) if item.get("type") == "blob"]


def main() -> int:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    with open(REGISTRY, encoding="utf-8") as f:
        registry = json.load(f)
    paths = {}
    for src in registry["sources"]:
        pat = re.compile(src["path_filter"], re.IGNORECASE)
        raw = github_tree(src["repo"], src["sha"], token)
        selected = sorted({p for p in raw if pat.search(p)})
        src["count"] = len(selected)
        paths[src["id"]] = selected
        print(f"== {src['id']}: {len(selected)} файлов ({src['repo']}@{src['sha'][:7]})")
    with open(REGISTRY, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)
    index = {
        "generated_by": "scripts/build_srt_index.py",
        "comment": "Инвентарь путей внешних SRT/CSV. НЕ содержит текстов субтитров.",
        "sources": registry["sources"],
        "paths": paths,
    }
    with open(INDEX, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Готово: {INDEX} ({os.path.getsize(INDEX)} байт)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
