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
from urllib.parse import quote

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


def build_love_batch(conn, official: dict | None = None) -> dict:
    """Отдельный блок из 50 подготовленных кандидатов «признание в любви и чувства».

    Основная очередь на 146 сцен не меняется: у этих кандидатов есть `batch_id`,
    поэтому `_best_candidate()` их игнорирует. У каждого отрывка свой candidate_id,
    таймкод, контекст, VK/Rutube-ссылка и локальная команда. `verified=1`
    по-прежнему ставится только через `/api/admin/moderation/confirm`.
    """
    official = official or load_official_videos()
    gate = float(load_taxonomy().get("gate_threshold", 0.45))
    rows = conn.execute(
        "SELECT tc.*, s.movie_title, s.movie_year, s.imdb_id, s.genre, s.description,"
        " s.why_fits, s.watch_url, s.version_note, s.verified AS scene_verified"
        " FROM timecode_candidates tc JOIN scenes s ON s.id=tc.scene_id"
        " WHERE tc.batch_id=? ORDER BY COALESCE(tc.batch_rank, tc.id), tc.id",
        (LOVE_BATCH_ID,),
    ).fetchall()
    items = []
    for cand in rows:
        title = cand["movie_title"]
        year = cand["movie_year"]
        start = int(cand["timecode_seconds"])
        end = int(cand["timecode_end_seconds"] or start + 25)
        start_label = seconds_to_label(start)
        end_label = seconds_to_label(end)
        feat = _features(cand)
        evidence_kind = feat.get("evidence_kind", "srt_window")
        is_external = evidence_kind == "external_orienteer"
        is_orienteer = evidence_kind in ("starting_orienteer", "external_orienteer")
        intent = intent_for(title, year)
        q = quote(f"{title} {year or ''}".strip())
        q_full = quote(f"{title} {year or ''} полный фильм".strip())
        vk_search_url = SERVICE_SEARCH["vkvideo"].format(q=q_full)
        rutube_search_url = SERVICE_SEARCH["rutube"].format(q=q_full)
        video = _official_video(title, year, cand["imdb_id"], official)
        if video:
            open_url = _deep_link(video, start)
            open_provider = "VK Видео" if video.get("template") == "vk_video" else "Rutube"
            open_kind = video.get("template") or "deep"
            open_note = (f"Официальная версия {open_provider} откроется сразу на "
                         f"{start_label}. {video.get('version_hint') or ''}")
        elif cand["watch_url"]:
            open_url = normalize_watch_url(cand["watch_url"], start) or cand["watch_url"]
            open_provider = "VK/Rutube watch_url"
            open_kind = "direct"
            open_note = "Ссылка сцены; таймкод добавлен, если это VK Видео или Rutube."
        else:
            open_url = vk_search_url
            open_provider = "VK Видео"
            open_kind = "vk_search"
            open_note = f"Поиск полной версии в VK Видео; ориентир {start_label} указан в карточке."
        link_candidate = {
            "movie_title": title, "movie_year": year, "imdb_id": cand["imdb_id"],
            "timecode_label": start_label, "timecode_seconds": start,
            "timecode_end_seconds": end, "watch_url": cand["watch_url"],
        }
        film_links = [
            {"label": l["label"], "url": l["url"], "note": (l.get("note") or "")[:120]}
            for l in build_moderation_links(link_candidate, official) if l.get("url")
        ][:6]
        film_url = film_links[0]["url"] if film_links else vk_search_url
        status = ("verified" if cand["scene_verified"] or cand["status"] == "approved"
                  else "rejected" if cand["status"] == "rejected" else "pending")
        items.append({
            "scene_id": cand["scene_id"], "movie_title": title, "movie_year": year,
            "imdb_id": cand["imdb_id"], "genre": cand["genre"],
            "description": cand["description"], "why_fits": cand["why_fits"],
            "verified": bool(cand["scene_verified"]), "status": status,
            "status_label": {"verified": "проверено", "rejected": "отклонено"}.get(
                status, "есть ориентир — к отсмотру" if is_orienteer
                else "есть таймкод — к отсмотру"),
            "batch_id": LOVE_BATCH_ID, "batch_rank": cand["batch_rank"],
            "batch_label": LOVE_BATCH_LABEL,
            "category": "love_confession", "category_label": LOVE_BATCH_LABEL,
            "primary_intent": feat.get("primary_intent", intent.get("primary_intent")),
            "secondary_intents": feat.get("secondary_intents", intent.get("secondary_intents", [])),
            "fragment_type": cand["fragment_type"] or intent.get("fragment_type", "dialogue"),
            "fragment_type_label": fragment_types().get(cand["fragment_type"] or intent.get("fragment_type", "dialogue"), ""),
            "curated_anchor": feat.get("curated_anchor", intent.get("curated_anchor", "")),
            "moment_label": cand["moment_label"] or feat.get("moment_label", ""),
            "context": cand["reason"] or feat.get("context", ""),
            "emotion_tags": feat.get("emotion_tags", []),
            "version_hint": feat.get("version_hint", "Сверь издание и укажи version_note."),
            "external_refs": feat.get("external_refs", []),
            "external_note": feat.get("external_note", ""),
            "evidence_kind": evidence_kind,
            "evidence_note": feat.get("evidence_note", ""),
            "source_label": ("Внешний ориентир" if is_external
                             else "Начальный ориентир" if is_orienteer
                             else "Локальный SRT"),
            "curated_orienteer": bool(feat.get("curated_orienteer")),
            "scene_version_note": cand["version_note"] or "",
            "candidate_id": cand["id"], "candidate_status": cand["status"],
            "candidate_source": cand["source"],
            "evidence_source": cand["source"].split(f"#{LOVE_BATCH_ID}")[0],
            "source_license": cand["source_license"],
            "evidence_sha1": cand["evidence_sha1"], "line_index": cand["line_index"],
            "intent_score": float(cand["intent_score"] or 0),
            "confidence": float(cand["confidence"] or 0),
            "consensus": int(cand["consensus"] or 1), "auto": int(cand["auto"] or 0),
            "duration_seconds": feat.get("duration_seconds", end - start),
            "score": float(cand["match_score"] or 0),
            "evidence_features": feat,
            "proposal_kind": ("external_orienteer" if is_external
                              else "starting_orienteer" if is_orienteer
                              else "love_batch"),
            "proposal_note": feat.get("evidence_note") or (
                "Внешний ориентир по узнаваемой сцене, не проверенный момент."
                if is_external else
                "Уникальный начальный ориентир, не проверенный момент."
                if is_orienteer else
                "Окно построено вокруг строки локального SRT с прямым/трогательным признанием."),
            "reason": cand["reason"] or "",
            "proposed_label": start_label, "proposed_seconds": start,
            "proposed_end_label": end_label, "proposed_end_seconds": end,
            "starting_label": start_label, "starting_seconds": start,
            "starting_kind": "external" if is_external else "estimate" if is_orienteer else "srt",
            "starting_note": ("Внешний ориентир Clip.Cafe по узнаваемой сцене; НЕ проверенный момент."
                              if is_external else
                              "Уникальный начальный ориентир по фильму и категории; НЕ проверенный момент."
                              if is_orienteer else
                              "Старт совпадает с подготовленным отрывком; НЕ проверенный момент."),
            "starting_open_url": open_url, "starting_command": f'mpv --start={start_label} "{LOCAL_PLACEHOLDER}"',
            "open_url": open_url, "open_provider": open_provider, "open_kind": open_kind,
            "open_note": open_note, "source_url": film_url,
            "film_url": film_url, "film_links": film_links,
            "local_command": f'mpv --start={start_label} "{LOCAL_PLACEHOLDER}"',
            "gate_threshold": gate,
            "curator_instruction": ("Открой ориентир в VK Видео или Rutube, поправь минуты под издание, "
                                    "укажи version_note и нажми «✓ Подтвердить»." if is_orienteer else
                                    "Открой момент в VK Видео или Rutube, поправь минуты под издание, "
                                    "укажи version_note и нажми «✓ Подтвердить»."),
            "search_url": vk_search_url, "vk_search_url": vk_search_url,
            "rutube_search_url": rutube_search_url,
            "youtube_search_url": None, "imdb_url": None,
        })
    stats = {
        "total": len(items),
        "pending": sum(1 for i in items if i["status"] == "pending"),
        "verified": sum(1 for i in items if i["status"] == "verified"),
        "rejected": sum(1 for i in items if i["status"] == "rejected"),
        "unique_scenes": len({(i["movie_title"], i["movie_year"]) for i in items}),
        "with_timecode": sum(1 for i in items if i["proposed_label"]),
        "with_open_url": sum(1 for i in items if i["open_url"]),
        "with_film_links": sum(1 for i in items if i["film_links"]),
        "with_local_command": sum(1 for i in items if i["local_command"]),
        "srt_evidence": sum(1 for i in items if i["evidence_kind"] == "srt_window"),
        "starting_orienteers": sum(1 for i in items if i["evidence_kind"] in ("starting_orienteer", "external_orienteer")),
        "external_orienteers": sum(1 for i in items if i["evidence_kind"] == "external_orienteer"),
        "with_external_refs": sum(1 for i in items if i["external_refs"]),
        "verified_by_human": False,
        "version_note_required": True,
    }
    return {"items": items, "stats": stats, "batch_id": LOVE_BATCH_ID, "label": LOVE_BATCH_LABEL}


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
