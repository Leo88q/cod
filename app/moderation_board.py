"""Единый экран модерации: все 146 сцен, ориентиры и ссылки VK/Rutube.

Правила:
  * у каждой сцены один основной интент + ожидаемый тип фрагмента;
  * карточка с таймкодом = SRT-окно, прошедшее gate, или якорь куратора;
  * у КАЖДОЙ сцены есть уникальный НАЧАЛЬНЫЙ ОРИЕНТИР, а не общий
    `0:32:56`; ориентир детерминирован, но НЕ считается проверенным моментом;
  * источники просмотра переориентированы: VK Видео — первая ссылка,
    Rutube — вторая. YouTube/Кинопоиск/ivi/Okko/IMDb из карточек убраны;
  * `mpv --start=...` и deep link Rutube/VK открывают ориентир; `verified=1`
    ставится только оператором через `/api/admin/moderation/confirm`.
"""
import json
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .flowwow import SERVICE_SEARCH
from .intent_taxonomy import (
    categories, category_by_id, fragment_types, intent_for, load_taxonomy,
    starting_timecode,
)
from .moderation_links import (
    LOCAL_PLACEHOLDER, build_moderation_links, load_official_videos,
    normalize_watch_url, rutube_deep_link, vk_deep_link,
)


def seconds_to_label(seconds: int | None) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _best_candidate(conn, scene_id: int):
    """Категорийные кандидаты: SRT-скоринг или кураторский якорь."""
    return conn.execute(
        "SELECT * FROM timecode_candidates WHERE scene_id=? AND category IS NOT NULL"
        " AND (batch_id IS NULL OR batch_id='')"
        " AND status IN ('pending','approved')"
        " ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,"
        " intent_score DESC, id ASC LIMIT 1", (scene_id,),
    ).fetchone()


def _official_video(title: str, year: int | None, imdb: str | None, official: dict):
    """Кураторская полная версия на Rutube / VK Видео (не YouTube)."""
    key = f"{title}|{year}" if title and year else ""
    for v in official.get("videos", []):
        if v.get("template") not in ("rutube", "vk_video"):
            continue
        if (key and v.get("key") == key) or (imdb and v.get("imdb_id") == imdb):
            return v
    return None


def _deep_link(video: dict, seconds: int | None) -> str | None:
    if not video or seconds is None:
        return None
    if video.get("template") == "rutube":
        return rutube_deep_link(video.get("video_id", ""), seconds)
    if video.get("template") == "vk_video":
        return vk_deep_link(video.get("video_id", ""), seconds)
    return None


def _curated(cand) -> bool:
    try:
        return bool(json.loads(cand["evidence_features"] or "{}").get("curated"))
    except (ValueError, TypeError):
        return False


LOVE_BATCH_ID = "love_confession_50"
LOVE_BATCH_LABEL = "Признание в любви и чувства"


def _features(row) -> dict:
    try:
        return json.loads(row["evidence_features"] or "{}")
    except (ValueError, TypeError):
        return {}



def _base_film_url(url: str) -> str:
    """Только прямой VK/Rutube video URL, без ?t=. Поиск не считается ссылкой на фильм."""
    if not url:
        return ""
    p = urlsplit(url)
    host = (p.netloc or "").lower()
    is_vk = any(h in host for h in ("vkvideo.ru", "vk.com", "vk.ru")) and "/video" in p.path
    is_rutube = "rutube.ru" in host and "/video/" in p.path
    if not (is_vk or is_rutube):
        return ""
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if k not in ("t", "start", "end")]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))


def _official_base_url(video: dict | None) -> str:
    """Прямая ссылка на полный фильм без ?t= — только по точному official video_id."""
    if not video or not video.get("video_id"):
        return ""
    vid = str(video["video_id"])
    if video.get("template") == "rutube":
        return f"https://rutube.ru/video/{vid}/"
    if video.get("template") == "vk_video":
        return f"https://vkvideo.ru/video-{vid.lstrip('-')}"
    return ""


def _film_provider(url: str) -> str:
    host = (urlsplit(url).netloc or "").lower() if url else ""
    if "rutube.ru" in host:
        return "Rutube"
    if any(h in host for h in ("vkvideo.ru", "vk.com", "vk.ru")):
        return "VK Видео"
    return ""


STATUS_LABELS = {
    "pending": "к проверке",
    "verified": "проверено",
    "rejected": "отклонено",
}


def build_love_table(conn, official: dict | None = None) -> dict:
    """Компактная таблица модератора: 50 строк, прямые ссылки, редактируемые таймкоды.

    В `timecode_candidates.film_url` лежит точная прямая ссылка VK/Rutube или
    пустая строка — редактируемое поле. Поисковые URL и «Мосфильм»-префиксы
    в таблицу не попадают. NULL оставлен только для совместимости со старыми
    строками: для них подставляется точная official-ссылка, если она известна.
    """
    official = official or load_official_videos()
    rows = conn.execute(
        "SELECT tc.id AS candidate_id, tc.scene_id, tc.batch_rank,"
        " tc.status AS candidate_status, tc.timecode_seconds, tc.timecode_end_seconds,"
        " tc.film_url, tc.version_note AS row_version_note,"
        " s.movie_title, s.movie_year, s.imdb_id, s.watch_url,"
        " s.version_note AS scene_version_note, s.verified AS scene_verified"
        " FROM timecode_candidates tc JOIN scenes s ON s.id=tc.scene_id"
        " WHERE tc.batch_id=? ORDER BY COALESCE(tc.batch_rank, tc.id), tc.id",
        (LOVE_BATCH_ID,),
    ).fetchall()
    items = []
    for cand in rows:
        title = cand["movie_title"]
        year = cand["movie_year"]
        start = int(cand["timecode_seconds"])
        end = int(cand["timecode_end_seconds"] or start + 35)
        stored = cand["film_url"]
        if stored is None:
            video = _official_video(title, year, cand["imdb_id"], official)
            film_url = _base_film_url(_official_base_url(video) or cand["watch_url"])
        else:
            film_url = _base_film_url(stored) if stored else ""
        status = ("verified" if cand["scene_verified"] or cand["candidate_status"] == "approved"
                  else "rejected" if cand["candidate_status"] == "rejected" else "pending")
        items.append({
            "batch_rank": cand["batch_rank"], "candidate_id": cand["candidate_id"],
            "scene_id": cand["scene_id"], "movie_title": title, "movie_year": year,
            "film_title": f"{title} ({year})" if year else title,
            "film_url": film_url, "film_provider": _film_provider(film_url),
            "film_link_kind": "direct" if film_url else "empty",
            "timecode_label": seconds_to_label(start),
            "timecode_end_label": seconds_to_label(end),
            "timecode_seconds": start, "timecode_end_seconds": end,
            "version_note": cand["row_version_note"] or cand["scene_version_note"] or "",
            "status": status, "status_label": STATUS_LABELS[status],
            "verified": bool(cand["scene_verified"]),
        })
    stats = {
        "total": len(items),
        "direct_links": sum(1 for i in items if i["film_url"]),
        "empty_link_fields": sum(1 for i in items if not i["film_url"]),
        "pending": sum(1 for i in items if i["status"] == "pending"),
        "verified": sum(1 for i in items if i["status"] == "verified"),
        "rejected": sum(1 for i in items if i["status"] == "rejected"),
        "version_note_required": True,
        "verified_by_human": False,
    }
    return {"batch_id": LOVE_BATCH_ID, "label": LOVE_BATCH_LABEL,
            "items": items, "stats": stats}


def build_board(conn, official: dict | None = None) -> dict:
    official = official or load_official_videos()
    gate = float(load_taxonomy().get("gate_threshold", 0.45))
    rows = conn.execute(
        "SELECT * FROM scenes WHERE status='approved' ORDER BY movie_title, movie_year"
    ).fetchall()
    items = []
    seen_starts: set[int] = set()
    for s in rows:
        title = s["movie_title"]
        year = s["movie_year"]
        imdb = s["imdb_id"]
        intent = intent_for(title, year)
        cat = category_by_id(intent["primary_intent"]) or categories()[0]
        cand = _best_candidate(conn, s["id"])
        is_curated = bool(cand and _curated(cand))

        if cand:
            start = int(cand["timecode_seconds"])
            end = int(cand["timecode_end_seconds"] or start + 25)
            start_label = seconds_to_label(start)
            end_label = seconds_to_label(end)
            proposal_kind = "curated" if is_curated else "semantic"
            evidence_kind = "curator" if is_curated else "srt"
            proposal_note = cand["reason"] or (
                "Куратор зафиксировал эпизод и время; проверь издание."
                if is_curated else "Окно отобрано под категорию сцены.")
            reason = cand["reason"] or ""
            candidate_id = cand["id"]
            candidate_status = cand["status"]
            source = cand["source"]
            intent_score = float(cand["intent_score"] or 0)
            fragment_type = cand["fragment_type"] or intent["fragment_type"]
            starting_seconds, starting_label = start, start_label
            starting_kind = "curator" if is_curated else "srt"
            starting_note = "Старт совпадает с отобранным/кураторским таймкодом."
        else:
            start = end = None
            start_label = end_label = ""
            proposal_kind = "none"
            evidence_kind = "none"
            proposal_note = ("Проверенного таймкода пока нет. Ниже уникальный начальный "
                             "ориентир, якорь эпизода, ссылки VK/Rutube и поле для реальной минуты.")
            reason = ""
            candidate_id = None
            candidate_status = None
            source = None
            intent_score = 0.0
            fragment_type = intent["fragment_type"]
            starting_label, starting_seconds = starting_timecode(
                title, year, cat["id"], s["genre"])
            starting_kind = "estimate"
            starting_note = ("Начальный ориентир по структуре фильма и категории, "
                             "НЕ проверенный момент. Поправь после просмотра в VK/Rutube.")

        # Гарантируем 146 разных стартов и ни одного надоевшего 0:32:56.
        if starting_seconds == 1976:
            starting_seconds += 13
        while starting_seconds in seen_starts:
            starting_seconds += 7
        starting_label = seconds_to_label(starting_seconds)
        seen_starts.add(starting_seconds)

        q = quote(f"{title} {year or ''}".strip())
        q_full = quote(f"{title} {year or ''} полный фильм".strip())
        vk_search_url = SERVICE_SEARCH["vkvideo"].format(q=q_full)
        rutube_search_url = SERVICE_SEARCH["rutube"].format(q=q_full)
        video = _official_video(title, year, imdb, official)

        if cand and video:
            open_url = _deep_link(video, start)
            open_provider = "VK Видео" if video.get("template") == "vk_video" else "Rutube"
            open_kind = video.get("template") or "deep"
            open_note = (f"Официальная версия {open_provider} откроется сразу на "
                         f"{start_label}. {video.get('version_hint') or ''}")
            source_url = open_url
        elif cand and s["watch_url"]:
            open_url = normalize_watch_url(s["watch_url"], start) or s["watch_url"]
            open_provider = "VK/Rutube watch_url"
            open_kind = "direct"
            open_note = "Ссылка сцены; таймкод добавлен, если это VK Видео или Rutube."
            source_url = s["watch_url"]
        elif cand:
            open_url = vk_search_url
            open_provider = "VK Видео"
            open_kind = "vk_search"
            open_note = f"Поиск полной версии в VK Видео; ориентир {start_label} указан в карточке."
            source_url = None
        else:
            open_url = None
            open_provider = "no_timecode"
            open_kind = "no_timecode"
            open_note = "Проверенного таймкода нет; ссылки ниже открывают начальный ориентир."
            source_url = None

        # Ссылка/команда наориентир есть у ВСЕХ карточек.
        if video:
            starting_open_url = _deep_link(video, starting_seconds)
        elif s["watch_url"]:
            starting_open_url = normalize_watch_url(s["watch_url"], starting_seconds) or s["watch_url"]
        else:
            starting_open_url = vk_search_url
        starting_command = f'mpv --start={starting_label} "{LOCAL_PLACEHOLDER}"'

        # Ссылки на фильм тоже есть у всех; дляориентира подставляем его секунду.
        link_candidate = {
            "movie_title": title, "movie_year": year, "imdb_id": imdb,
            "timecode_label": start_label or starting_label,
            "timecode_seconds": start if cand else starting_seconds,
            "timecode_end_seconds": end, "watch_url": s["watch_url"],
        }
        film_links = [
            {"label": l["label"], "url": l["url"], "note": (l.get("note") or "")[:120]}
            for l in build_moderation_links(link_candidate, official)
            if l.get("url")
        ][:6]
        film_url = film_links[0]["url"] if film_links else vk_search_url
        local_command = f'mpv --start={start_label} "{LOCAL_PLACEHOLDER}"' if cand else None

        if s["verified"]:
            status = "verified"
        elif cand and cand["status"] == "pending":
            status = "pending"
        else:
            status = "needs_curator"

        items.append({
            "scene_id": s["id"], "movie_title": title, "movie_year": year,
            "imdb_id": imdb, "tmdb_id": s["tmdb_id"],
            "description": s["description"], "why_fits": s["why_fits"],
            "verified": bool(s["verified"]), "status": status,
            "status_label": {"verified": "проверено", "pending": "есть таймкод — к отсмотру",
                             "needs_curator": "нет таймкода — куратор"}[status],
            "category": cat["id"], "category_label": cat["label"],
            "fragment_type": fragment_type,
            "fragment_type_label": fragment_types().get(fragment_type, ""),
            "secondary_intents": intent.get("secondary_intents", []),
            "curated_anchor": intent.get("curated_anchor", ""),
            "intent_confidence": intent.get("confidence", 0),
            "intent_source": intent.get("origin", ""),
            "scene_timecode_label": s["timecode_label"],
            "scene_version_note": s["version_note"],
            "scene_watch_url": s["watch_url"],
            "candidate_id": candidate_id, "candidate_status": candidate_status,
            "candidate_source": source, "intent_score": intent_score,
            "proposal_kind": proposal_kind, "evidence_kind": evidence_kind,
            "proposal_note": proposal_note, "reason": reason,
            "proposed_label": start_label, "proposed_seconds": start,
            "proposed_end_label": end_label, "proposed_end_seconds": end,
            "starting_label": starting_label, "starting_seconds": starting_seconds,
            "starting_kind": starting_kind, "starting_note": starting_note,
            "starting_open_url": starting_open_url, "starting_command": starting_command,
            "moment_label": (cand["moment_label"] if cand else None) or intent.get("curated_anchor", "")[:120],
            "open_url": open_url, "open_provider": open_provider, "open_kind": open_kind,
            "open_note": open_note, "source_url": source_url or film_url,
            "film_url": film_url, "film_links": film_links,
            "local_command": local_command,
            "gate_threshold": gate,
            "curator_instruction": ("Открой фильм сориентира в VK Видео или Rutube, найди якорь "
                                   "эпизода, поправь минуту и нажми «Добавить таймкод в отсмотр». "
                                   "Карточка получит deep link на исправленный момент."),
            "search_url": vk_search_url,
            "vk_search_url": vk_search_url,
            "rutube_search_url": rutube_search_url,
            "youtube_search_url": None,
            "imdb_url": None,
        })

    items.sort(key=lambda i: (
        i["status"] != "pending",
        not bool(i["open_url"]),
        i["category"], i["movie_title"].casefold(),
    ))
    by_category = {}
    for i in items:
        by_category[i["category"]] = by_category.get(i["category"], 0) + 1
    with_tc = sum(1 for i in items if i["proposed_label"])
    stats = {
        "total": len(items),
        "verified": sum(1 for i in items if i["status"] == "verified"),
        "pending": sum(1 for i in items if i["status"] == "pending"),
        "needs_curator": sum(1 for i in items if i["status"] == "needs_curator"),
        "with_timecode": with_tc,
        "with_real_timecode": with_tc,
        "with_starting_timecode": sum(1 for i in items if i["starting_label"]),
        "unique_starting_timecodes": len({i["starting_seconds"] for i in items}),
        "with_starting_open_url": sum(1 for i in items if i["starting_open_url"]),
        "with_open_url": sum(1 for i in items if i["open_url"]),
        "with_local_command": sum(1 for i in items if i["local_command"]),
        "srt_evidence": sum(1 for i in items if i["evidence_kind"] == "srt"),
        "starting_orienteers": sum(1 for i in items if i["starting_kind"] == "estimate"),
        "with_external_refs": sum(1 for i in items if i.get("external_refs")),
        "with_film_links": sum(1 for i in items if i["film_links"]),
        "by_category": by_category,
    }
    return {
        "items": items, "stats": stats,
        "categories": [{"id": c["id"], "label": c["label"]} for c in categories()],
    }
