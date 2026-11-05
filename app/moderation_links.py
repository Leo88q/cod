"""Построение ссылок для РУЧНОЙ модерации таймкодов.

Задача — открыть фильм сразу на нужном моменте, не привязываясь к платным
подпискам и не используя пиратские зеркала. Алгоритм (см. docs/15-moderation-links.md):

1. берём `timecode_seconds` кандидата;
2. ищем легальную бесплатную полную версию в `official_free_videos.json`
   (официальные каналы студий, YouTube/RuTube/официальные страницы);
3. для YouTube генерируем deep link `&t=<секунда>s` — сразу на моменте;
4. всегда добавляем команду для локальной легальной копии (mpv/VLC/IINA),
   поиск в легальных сервисах и IMDb/TMDB для идентификации издания;
5. `version_note` остаётся обязательным: реставрация/монтаж могут сдвинуть таймкод.

Ссылки НЕ подтверждают таймкод и НЕ ставят `verified=1`.
"""
import json
import os
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .flowwow import SERVICE_SEARCH

LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "timecode_library")
OFFICIAL_PATH = os.path.join(LIB_DIR, "official_free_videos.json")

LOCAL_PLACEHOLDER = "/path/to/film.mkv"


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


def youtube_deep_link(video_id: str, seconds: int | None) -> str:
    base = f"https://www.youtube.com/watch?v={video_id}"
    return f"{base}&t={int(seconds)}s" if seconds is not None else base


def normalize_watch_url(url: str, seconds: int | None) -> str | None:
    """Добавляет таймкод к уже вставленному оператором watch_url, если провайдер
    поддерживает стабильный публичный deep link. Иначе возвращает None."""
    if not url or seconds is None:
        return None
    p = urlsplit(url)
    host = (p.netloc or "").lower()
    if "youtu.be" in host:
        vid = p.path.strip("/")
        return youtube_deep_link(vid, seconds)
    if "youtube.com" in host:
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k not in ("t", "start", "end")]
        q.append(("t", f"{int(seconds)}s"))
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))
    if "vimeo.com" in host:
        return urlunsplit((p.scheme, p.netloc, p.path, p.query, f"t={int(seconds)}s"))
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


def build_moderation_links(candidate: dict, official: dict | None = None) -> list[dict]:
    """-> отсортированный список {kind,label,url?,commands?,note?,priority,seconds?}.

    `candidate` — строка очереди `timecode_candidates` + поля сцены
    (`movie_title`, `movie_year`, `imdb_id`, опционально `watch_url`).
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
    key = f"{title}|{year}" if title and year else ""
    q = quote(f"{title} {year or ''}".strip())
    q_official = quote(f"Мосфильм {title} {year or ''} полный фильм".strip())
    templates = official.get("provider_templates", {})
    out: list[dict] = []

    def add(kind: str, priority: int, text: str, **kw) -> None:
        out.append({"kind": kind, "priority": priority, "label": text,
                    "seconds": seconds, "end_seconds": end_seconds, **kw})

    # 1. Легальные бесплатные полные версии с deep link на нужную секунду.
    for v in official.get("videos", []):
        if key and v.get("key") != key and not (imdb and v.get("imdb_id") == imdb):
            continue
        tpl = templates.get(v.get("template"), "")
        if not tpl:
            continue
        try:
            url = tpl.format(video_id=v.get("video_id", ""), seconds=seconds or 0,
                             url=v.get("url", ""))
        except (KeyError, ValueError):
            continue
        is_deep = v.get("template") == "youtube" and seconds is not None
        note = v.get("version_hint") or ""
        if is_deep:
            note += " Откроется сразу на предложенном моменте; всё равно сверь издание и заполни version_note."
        else:
            note += " Прямой deep link с секундой здесь не гарантирован; перемотай вручную."
        add("official_deep" if is_deep else "official_page", 1 if is_deep else 2,
            ("▶ " if is_deep else "📄 ") + f"{v.get('provider', 'официальный источник')}"
            + (f" · {label}" if is_deep and label else ""),
            url=url, note=note.strip(), evidence_url=v.get("evidence_url"),
            provider=v.get("provider"), requires_version_check=official.get("requires_version_check", True))

    # 2. Уже вставленный оператором watch_url — нормализуем до deep link.
    watch = candidate.get("watch_url")
    if watch:
        deep = normalize_watch_url(watch, seconds)
        add("manual_deep" if deep else "manual", 1 if deep else 2,
            "▶ Твой watch_url" + (" · сразу на моменте" if deep else " (без авто-перемотки)"),
            url=deep or watch,
            note="Прямая ссылка из карточки сцены; таймкод добавлен автоматически только для YouTube/Vimeo."
            if deep else "Провайдер не имеет стабильного публичного deep link — перемотай вручную.")

    # 3. Локальная легальная копия: самый независимый от подписок путь.
    cmds = local_commands(seconds, label)
    if cmds:
        add("local", 3, "⏱ Открыть локальный файл на моменте",
            commands=cmds,
            note=f"Замени {LOCAL_PLACEHOLDER} на путь к своей легальной копии. Подписка не нужна.")

    # 4. Поиск на официальных бесплатных площадках студий.
    for s in official.get("sources", []):
        template = s.get("search_url") or s.get("url") or ""
        url = template.format(q=q_official) if "{q}" in template else template
        add("official_search", 4, f"🔍 {s.get('name', s.get('id'))}",
            url=url, note="Официальный бесплатный канал/страница; найди полную версию и версию издания.")

    # 5. Легальные сервисы (часть требует подписку) — как fallback.
    for service in ("kinopoisk", "ivi", "okko", "vkvideo", "youtube"):
        tpl = SERVICE_SEARCH.get(service)
        if not tpl:
            continue
        add("search", 5, f"🔍 {service}", url=tpl.format(q=q),
            note="Поиск фильма в легальном сервисе. Может требовать подписку; таймкод не подставляется автоматически.")

    # 6. Идентификация издания.
    if imdb:
        add("identify", 6, "📄 IMDb", url=f"https://www.imdb.com/title/{imdb}/",
            note="Сверь год/режиссёра/версию перед подтверждением.")
    add("identify", 6, "📄 TMDB поиск",
        url=f"https://www.themoviedb.org/search?query={q}",
        note="Поиск метаданных, не просмотр.")

    # убираем дубли URL, сохраняя приоритет
    seen, unique = set(), []
    for link in sorted(out, key=lambda x: (x["priority"], x["label"])):
        marker = link.get("url") or link.get("commands", [{}])[0].get("command")
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(link)
    return unique


def moderation_targets(conn, official: dict | None = None) -> list[dict]:
    """Сцены каталога, для которых есть легальная бесплатная полная версия.

    Нужны для РУЧНОГО создания кандидата там, где SRT-зеркала ничего не нашли:
    оператор открывает фильм, находит момент сам и вписывает таймкод.
    """
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
        tpl = templates.get(v.get("template"), "")
        if v.get("template") == "youtube":
            url = youtube_deep_link(v.get("video_id", ""), 0)
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
        # Если на один фильм несколько записей, берём YouTube deep link.
        old = by_key.get(key)
        if old is None or (has_deep and not old["has_deep_link"]):
            by_key[key] = candidate
    return sorted(by_key.values(), key=lambda x: (not x["has_deep_link"], x["pending_candidates"], x["movie_title"]))


def moderation_coverage(official: dict | None = None) -> dict:
    official = official or load_official_videos()
    videos = official.get("videos", [])
    deep_keys = {v.get("key") for v in videos if v.get("template") == "youtube"}
    return {
        "official_videos": len(videos),
        "scenes_with_official_deep": len(deep_keys),
        "requires_version_check": official.get("requires_version_check", True),
        "verified_by_human": official.get("verified_by_human", False),
        "sources": [s.get("id") for s in official.get("sources", [])],
    }
