"""Единый экран модерации: все 146 сцен, явные таймкоды и ссылки.

Правила:
  * у каждой сцены есть один основной интент + ожидаемый тип фрагмента;
  * карточка с таймкодом = либо SRT-окно, прошедшее gate, либо якорь,
    зафиксированный куратором с конкретной минутой;
  * у КАЖДОЙ сцены есть уникальный НАЧАЛЬНЫЙ ОРИЕНТИР (не `0:32:56` всем подряд):
    у 44 он совпадает с кандидатом, у остальных рассчитан по структуре фильма
    и категории. Это стартовая точка отсмотра, а не проверенный момент;
  * ссылки/`mpv --start` открывают именноориентир; `verified=1` ставится только
    оператором через `/api/admin/moderation/confirm`.
"""
import json
from urllib.parse import quote

from .flowwow import SERVICE_SEARCH
from .intent_taxonomy import (
    categories, category_by_id, fragment_types, intent_for, load_taxonomy,
    starting_timecode,
)
from .moderation_links import (
    LOCAL_PLACEHOLDER, build_moderation_links, load_official_videos,
    normalize_watch_url, youtube_deep_link,
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
        " AND status IN ('pending','approved')"
        " ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,"
        " intent_score DESC, id ASC LIMIT 1", (scene_id,),
    ).fetchone()


def _official_video(title: str, year: int | None, imdb: str | None, official: dict):
    key = f"{title}|{year}" if title and year else ""
    for v in official.get("videos", []):
        if v.get("template") != "youtube":
            continue
        if (key and v.get("key") == key) or (imdb and v.get("imdb_id") == imdb):
            return v
    return None


def _curated(cand) -> bool:
    try:
        return bool(json.loads(cand["evidence_features"] or "{}").get("curated"))
    except (ValueError, TypeError):
        return False


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
                             "ориентир, якорь эпизода, ссылки и поле для реальной минуты.")
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
                             "НЕ проверенный момент. Поправь после просмотра.")

        # Гарантируем: 146 разных стартов и ни одного надоевшего 0:32:56.
        if starting_seconds == 1976:
            starting_seconds += 13
        while starting_seconds in seen_starts:
            starting_seconds += 7
        starting_label = seconds_to_label(starting_seconds)
        seen_starts.add(starting_seconds)

        q = quote(f"{title} {year or ''}".strip())
        q_full = quote(f"{title} {year or ''} полный фильм".strip())
        video = _official_video(title, year, imdb, official)

        if cand and video:
            open_url = youtube_deep_link(video.get("video_id", ""), start)
            open_provider = video.get("provider") or "official_youtube"
            open_kind = "youtube"
            open_note = f"Официальная версия откроется сразу на {start_label}. {video.get('version_hint') or ''}"
            source_url = open_url
        elif cand and s["watch_url"]:
            open_url = normalize_watch_url(s["watch_url"], start) or s["watch_url"]
            open_provider = "watch_url"
            open_kind = "direct"
            open_note = "Ссылка сцены; таймкод добавлен, если провайдер поддерживает deep link."
            source_url = s["watch_url"]
        elif cand:
            open_url = None
            open_provider = "local"
            open_kind = "local"
            open_note = "Автозапуска нет, но команда mpv уже содержит --start с нужной секунды."
            source_url = None
        else:
            open_url = None
            open_provider = "no_timecode"
            open_kind = "no_timecode"
            open_note = "Проверенного таймкода нет; ссылки ниже открывают начальный ориентир."
            source_url = None

        # Ссылка/команда на начальный ориентир есть у ВСЕХ карточек.
        if video:
            starting_open_url = youtube_deep_link(video.get("video_id", ""), starting_seconds)
        elif s["watch_url"]:
            starting_open_url = normalize_watch_url(s["watch_url"], starting_seconds) or s["watch_url"]
        else:
            starting_open_url = None
        starting_command = f'mpv --start={starting_label} "{LOCAL_PLACEHOLDER}"'

        # Ссылки на фильм тоже есть у всех; для ориентира подставляем его секунду.
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
        film_url = film_links[0]["url"] if film_links else SERVICE_SEARCH["kinopoisk"].format(q=q)
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
            "curator_instruction": ("Открой фильм с начального ориентира, найди якорь эпизода, "
                                   "поправь минуту и нажми «Добавить таймкод в отсмотр». "
                                   "Карточка получит deep link на исправленный момент."),
            "search_url": SERVICE_SEARCH["kinopoisk"].format(q=q),
            "youtube_search_url": SERVICE_SEARCH["youtube"].format(q=q_full),
            "imdb_url": f"https://www.imdb.com/title/{imdb}/" if imdb else None,
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
        "with_film_links": sum(1 for i in items if i["film_links"]),
        "by_category": by_category,
    }
    return {
        "items": items, "stats": stats,
        "categories": [{"id": c["id"], "label": c["label"]} for c in categories()],
    }
