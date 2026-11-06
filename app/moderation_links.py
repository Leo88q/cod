"""Построение ссылок для РУЧНОЙ модерации таймкодов.

По решению куратора площадки переориентированы: **VK Видео — основной
источник, Rutube — второй**. YouTube, Vimeo, Кинопоиск, ivi, Okko, IMDb/TMDB
и отдельные страницы студий как ссылки просмотра больше не выдаются.

Алгоритм (см. docs/15-moderation-links.md):

1. берём целевую секунду (`timecode_seconds` или начальный ориентир);
2. первой всегда идёт ссылка VK Видео:deep link, если есть проверенный
   куратором `vk_video` ID, иначе поиск по официальному каналу «Мосфильм»;
3. второй идёт Rutube: `?t=<секунда>` для 10 кураторских полных версий
   Мосфильма, иначе поиск по официальному Rutube-каналу;
4. `watch_url` оператора нормализуется только если он уже ведёт на VK/Rutube;
5. локальная легальная копия остаётся как независимый способ открытьориентир
   через `mpv --start=...` / VLC / IINA;
6. `version_note` обязателен: реставрация, серия и монтаж могут сдвинуть минуты.

Формат `?t=...` у VK-страниц подтверждён разбором плеера и/page links,
у Rutube — документацией embed-плеера. Прямой deep link не гарантирован для
поисковых ссылок; ориентир там показан в подписи и кнопке локальной команды.

Ссылки НЕ подтверждают таймкод и НЕ ставят `verified=1`.
"""
import json
import os
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .flowwow import SERVICE_SEARCH

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "timecode_library")
OFFICIAL_PATH = os.path.join(LIB_DIR, "official_free_videos.json")

LOCAL_PLACEHOLDER = "/path/to/film.mkv"
VK_SEARCH_URL = "https://vkvideo.ru/search?query={q}"
RUTUBE_SEARCH_URL = "https://rutube.ru/search/?query={q}"


def load_official_videos() -> dict:
    if not os.path.exists(OFFICIAL_PATH):
        return {"videos": [], "sources": [], "provider_templates": {}}
    with open(OFFICIAL_PATH, encoding="utf-8") as f:
        return json.load(f)


def tc_to_seconds(label: str | None) -> int | None:
    """'0:32:56' / '1:23:45' -> секунды."""
    if not label:
        return None
    parts = str(label).strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        h, m, s = nums
    elif len(nums) == 2:
        h, m, s = 0, nums[0], nums[1]
    elif len(nums) == 1:
        h, m, s = 0, 0, nums[0]
    else:
        return None
    return h * 3600 + m * 60 + s


def vk_deep_link(video_id: str, seconds: int | None) -> str:
    """VK Видео: `https://vkvideo.ru/video-<owner>_<id>?t=<секунды>s`."""
    # В VK URL минус сообщества уже зашит в префиксе `video-`; дубли не нужны.
    vid = str(video_id).lstrip("-")
    base = f"https://vkvideo.ru/video-{vid}"
    return f"{base}?t={int(seconds)}s" if seconds is not None else base


def rutube_deep_link(video_id: str, seconds: int | None) -> str:
    """Rutube: `https://rutube.ru/video/<uuid>/?t=<секунды>`."""
    base = f"https://rutube.ru/video/{video_id}/"
    return f"{base}?t={int(seconds)}" if seconds is not None else base


def normalize_watch_url(url: str, seconds: int | None) -> str | None:
    """Добавляет таймкод к `watch_url`, только если это VK Видео или Rutube."""
    if not url or seconds is None:
        return None
    p = urlsplit(url)
    host = (p.netloc or "").lower()
    if ("vkvideo.ru" in host or "vk.com" in host or "vk.ru" in host) and \
            ("/video" in p.path or "video_ext.php" in p.path):
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k not in ("t", "start", "end")]
        q.append(("t", f"{int(seconds)}s"))
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))
    if "rutube.ru" in host and ("/video/" in p.path or "/play/embed/" in p.path):
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k not in ("t", "start", "end")]
        q.append(("t", str(int(seconds))))
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))
    return None


def local_commands(seconds: int | None, label: str | None) -> list[dict]:
    if seconds is None:
        return []
    if label:
        start = label
    else:
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        start = f"{h}:{m:02d}:{s:02d}"
    return [
        {"app": "mpv", "command": f'mpv --start={start} "{LOCAL_PLACEHOLDER}"'},
        {"app": "VLC", "command": f'vlc --start-time={seconds} "{LOCAL_PLACEHOLDER}"'},
        {"app": "IINA (macOS)", "command": f'iina --start-time={seconds} "{LOCAL_PLACEHOLDER}"'},
    ]


def _source_url(official: dict, source_id: str, default: str, q: str) -> str:
    for s in official.get("sources", []):
        if s.get("id") != source_id:
            continue
        tpl = s.get("search_url") or s.get("url") or default
        return tpl.format(q=q) if "{q}" in tpl else tpl
    return default.format(q=q)


def _deep_video(official: dict, candidate: dict, template: str) -> dict | None:
    title = (candidate.get("movie_title") or "").strip()
    year = candidate.get("movie_year")
    imdb = candidate.get("imdb_id")
    key = f"{title}|{year}" if title and year else ""
    for v in official.get("videos", []):
        if v.get("template") != template:
            continue
        if (key and v.get("key") == key) or (imdb and v.get("imdb_id") == imdb):
            return v
    return None


def build_moderation_links(candidate: dict, official: dict | None = None) -> list[dict]:
    """-> список {kind,label,url?,commands?,note?,priority,seconds?,deep?}.

    Только VK Видео + Rutube + локальная легальная копия. Никаких других
    площадок, платных fallback'ов и идентификационных ссылок.
    """
    official = official or load_official_videos()
    title = (candidate.get("movie_title") or "").strip()
    year = candidate.get("movie_year")
    imdb = candidate.get("imdb_id")
    label = candidate.get("timecode_label")
    seconds = candidate.get("timecode_seconds")
    if seconds is None:
        seconds = tc_to_seconds(label)
    if seconds is not None:
        seconds = int(seconds)
    end_seconds = candidate.get("timecode_end_seconds")
    q = quote(f"{title} {year or ''}".strip())
    q_official = quote(f"Мосфильм {title} {year or ''} полный фильм".strip())
    vk_search = _source_url(official, "vk_mosfilm", VK_SEARCH_URL, q_official)
    rutube_search = _source_url(official, "rutube_mosfilm", RUTUBE_SEARCH_URL, q_official)
    vk_video = _deep_video(official, candidate, "vk_video")
    rutube_video = _deep_video(official, candidate, "rutube")
    out: list[dict] = []

    def add(kind: str, priority: int, text: str, **kw) -> None:
        out.append({"kind": kind, "priority": priority, "label": text,
                    "seconds": seconds, "end_seconds": end_seconds, **kw})

    # 1. VK Видео — основной источник.
    if vk_video:
        add("official_deep", 0, "▶ VK Видео" + (f" · {label}" if label else ""),
            url=vk_deep_link(vk_video.get("video_id", ""), seconds), deep=True,
            note="Официальный VK-канал; откроется сразу на ориентире. Всё равно сверь издание и заполни version_note.",
            evidence_url=vk_video.get("evidence_url"), provider="vk_mosfilm",
            requires_version_check=official.get("requires_version_check", True))
    else:
        add("search", 1, f"🔍 VK Видео · {title} {year or ''}".strip(),
            url=vk_search, deep=False,
            note=f"Найди полную версию на официальном канале Мосфильм.ориентир: {label or '—'}; deep link с секундой не гарантирован.",
            provider="vk_mosfilm",
            requires_version_check=official.get("requires_version_check", True))

    # 2. Rutube — второй источник / deep link для кураторских полных версий.
    if rutube_video:
        add("official_deep", 2, "▶ Rutube" + (f" · {label}" if label else ""),
            url=rutube_deep_link(rutube_video.get("video_id", ""), seconds), deep=True,
            note=(rutube_video.get("version_hint") or "Официальный Rutube-канал Мосфильм.")
                 + " Откроется сразу на ориентире; сверь серию/издание и заполни version_note.",
            evidence_url=rutube_video.get("evidence_url"),
            provider=rutube_video.get("provider") or "rutube_mosfilm",
            requires_version_check=official.get("requires_version_check", True))
    else:
        add("search", 3, f"🔍 Rutube · {title} {year or ''}".strip(),
            url=rutube_search, deep=False,
            note=f"Найди полную версию на официальном Rutube-канале Мосфильм.ориентир: {label or '—'}; deep link с секундой не гарантирован.",
            provider="rutube_mosfilm",
            requires_version_check=official.get("requires_version_check", True))

    # 3. Операторская ссылка — только если она уже ведёт на VK/Rutube.
    watch = candidate.get("watch_url")
    if watch:
        deep = normalize_watch_url(watch, seconds)
        if deep:
            add("manual_deep", 4, "▶ Твой watch_url · VK/Rutube, сразу на моменте",
                url=deep, deep=True,
                note="Прямая ссылка из карточки сцены; таймкод добавлен для VK/Rutube.")

    # 4. Локальная легальная копия — не сторонний источник, а свой файл.
    cmds = local_commands(seconds, label)
    if cmds:
        add("local", 5, "⏱ Открыть локальный файл наориентире",
            commands=cmds, deep=True,
            note=f"Замени {LOCAL_PLACEHOLDER} на путь к своей легальной копии. Подписка не нужна.")

    seen, unique = set(), []
    for link in sorted(out, key=lambda x: (x["priority"], x["label"])):
        marker = link.get("url") or link.get("commands", [{}])[0].get("command")
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(link)
    return unique


def moderation_targets(conn, official: dict | None = None) -> list[dict]:
    """Сцены каталога с кураторским Rutube/VK deep link для ручной проверки."""
    official = official or load_official_videos()
    templates = official.get("provider_templates", {})
    by_key: dict[str, dict] = {}
    for v in official.get("videos", []):
        key = v.get("key") or ""
        if "|" not in key:
            continue
        title, raw_year = key.rsplit("|", 1)
        try:
            year = int(raw_year)
        except ValueError:
            continue
        tpl_name = v.get("template")
        tpl = templates.get(tpl_name, "")
        if tpl_name == "rutube":
            url = rutube_deep_link(v.get("video_id", ""), 0)
            has_deep = True
        elif tpl_name == "vk_video":
            url = vk_deep_link(v.get("video_id", ""), 0)
            has_deep = True
        elif tpl:
            try:
                url = tpl.format(video_id=v.get("video_id", ""), url=v.get("url", ""), seconds=0)
            except (KeyError, ValueError):
                continue
            has_deep = False
        else:
            continue
        row = conn.execute(
            "SELECT id, imdb_id FROM scenes WHERE movie_title=? AND movie_year=?", (title, year)
        ).fetchone()
        if not row:
            continue
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM timecode_candidates WHERE scene_id=? AND status='pending'",
            (row["id"],),
        ).fetchone()["n"]
        candidate = {
            "scene_id": row["id"], "key": key, "movie_title": title, "movie_year": year,
            "imdb_id": row["imdb_id"] or v.get("imdb_id"), "url": url,
            "provider": v.get("provider"), "has_deep_link": has_deep,
            "version_hint": v.get("version_hint"), "evidence_url": v.get("evidence_url"),
            "pending_candidates": pending,
        }
        old = by_key.get(key)
        if old is None or (has_deep and not old["has_deep_link"]):
            by_key[key] = candidate
    return sorted(by_key.values(), key=lambda x: (not x["has_deep_link"], x["pending_candidates"], x["movie_title"]))


def moderation_coverage(official: dict | None = None) -> dict:
    official = official or load_official_videos()
    videos = official.get("videos", [])
    deep_keys = {v.get("key") for v in videos
                 if v.get("template") in ("rutube", "vk_video")}
    return {
        "official_videos": len(videos),
        "scenes_with_official_deep": len(deep_keys),
        "requires_version_check": official.get("requires_version_check", True),
        "verified_by_human": official.get("verified_by_human", False),
        "sources": [s.get("id") for s in official.get("sources", [])],
        "vk_search": SERVICE_SEARCH.get("vkvideo", VK_SEARCH_URL),
        "rutube_search": SERVICE_SEARCH.get("rutube", RUTUBE_SEARCH_URL),
    }
