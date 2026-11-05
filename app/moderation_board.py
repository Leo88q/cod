"""Единый экран модерации: ВСЕ сцены каталога в одной очереди.

Раньше админка показывала только `timecode_candidates` (62 сцены), а для
остальных предлагала «найди момент сам». Здесь каждая approved-сцена получает
карточку с уже подставленным отрезком и ссылкой/командой, которая открывает
фильм на этом времени. Оператор НЕ ищет момент с нуля: он открывает, смотрит,
при необходимости поправляет начало/конец и жмёт «✓ Подтвердить».

Честная типизация предложения:
  * `srt`   — реальный time fact из субтитров (GitHub/OPUS);
  * `draft` — детерминированный ЧЕРНОВИК для сцен без субтитров, ЯВНО помечен;
              это не «истина», а конкретная точка, с которой удобно начать отсмотр.
`verified=1` ставится только действием оператора.
"""
import hashlib
from urllib.parse import quote

from .flowwow import SERVICE_SEARCH
from .moderation_links import (
    LOCAL_PLACEHOLDER, load_official_videos, normalize_watch_url, youtube_deep_link,
)


def seconds_to_label(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def deterministic_proposal(title: str, year: int | None) -> tuple[int, int]:
    """Стабильный ЧЕРНОВИК отрезка, чтобы не открывать фильм с нуля.

    НЕ претендует на знание сюжета: это лишь конкретная точка для отсмотра.
    Один и тот же фильм всегда получает одно и то же предложение.
    """
    h = int(hashlib.sha1(f"{title}|{year or ''}".encode("utf-8")).hexdigest()[:8], 16)
    start = 420 + h % 5400          # 07:00 … 96:59
    end = start + 25 + (h // 7) % 20  # +25…44 сек
    return start, end


def _best_candidate(conn, scene_id: int):
    return conn.execute(
        "SELECT * FROM timecode_candidates WHERE scene_id=?"
        " ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,"
        " confidence DESC, id ASC LIMIT 1", (scene_id,),
    ).fetchone()


def _official_video(title: str, year: int | None, imdb: str | None, official: dict):
    key = f"{title}|{year}" if title and year else ""
    for v in official.get("videos", []):
        if v.get("template") != "youtube":
            continue
        if (key and v.get("key") == key) or (imdb and v.get("imdb_id") == imdb):
            return v
    return None


def build_board(conn, official: dict | None = None) -> dict:
    official = official or load_official_videos()
    rows = conn.execute(
        "SELECT * FROM scenes WHERE status='approved' ORDER BY movie_title, movie_year"
    ).fetchall()
    items = []
    for s in rows:
        title = s["movie_title"]
        year = s["movie_year"]
        imdb = s["imdb_id"]
        cand = _best_candidate(conn, s["id"])
        if cand:
            start = int(cand["timecode_seconds"])
            end = int(cand["timecode_end_seconds"] or start + 25)
            proposal_kind = "srt"
            proposal_note = "Кандидат из субтитров: время уже извлечено. Сверь издание и поправь при смещении."
            candidate_id = cand["id"]
            candidate_status = cand["status"]
            source = cand["source"]
            confidence = cand["confidence"]
            consensus = cand["consensus"]
            moment_label = cand["moment_label"] or s["description"]
        else:
            start, end = deterministic_proposal(title, year)
            proposal_kind = "draft"
            proposal_note = ("ЧЕРНОВИК: субтитров для этого фильма нет. Время подставлено," 
                             " чтобы открыть сразу на конкретном отрезке, а не с нуля. Обязательно поправь после просмотра.")
            candidate_id = None
            candidate_status = None
            source = "moderation-board-draft"
            confidence = 0.0
            consensus = 0
            moment_label = s["description"]

        start_label = seconds_to_label(start)
        end_label = seconds_to_label(end)
        video = _official_video(title, year, imdb, official)
        q = quote(f"{title} {year or ''}".strip())
        q_full = quote(f"{title} {year or ''} полный фильм".strip())

        if video:
            open_url = youtube_deep_link(video.get("video_id", ""), start)
            open_provider = video.get("provider") or "official_youtube"
            open_kind = "youtube"
            open_note = f"Официальная бесплатная версия откроется сразу на {start_label}. {video.get('version_hint') or ''}"
            source_url = open_url
        elif s["watch_url"]:
            open_url = normalize_watch_url(s["watch_url"], start) or s["watch_url"]
            open_provider = "watch_url"
            open_kind = "direct"
            open_note = "Прямая ссылка сцены; таймкод добавлен автоматически, если провайдер поддерживает deep link."
            source_url = s["watch_url"]
        else:
            open_url = None
            open_provider = "local"
            open_kind = "local"
            open_note = ("Легального автозапуска у этого сервиса нет. Команда ниже уже содержит --start: "
                         "замени путь к своей легальной копии — искать момент с нуля не нужно.")
            source_url = None

        local_command = f'mpv --start={start_label} "{LOCAL_PLACEHOLDER}"'
        if s["verified"]:
            status = "verified"
        elif cand and cand["status"] == "pending":
            status = "pending"
        else:
            status = "draft"

        items.append({
            "scene_id": s["id"], "movie_title": title, "movie_year": year,
            "imdb_id": imdb, "tmdb_id": s["tmdb_id"],
            "description": s["description"], "why_fits": s["why_fits"],
            "verified": bool(s["verified"]), "status": status,
            "scene_timecode_label": s["timecode_label"],
            "scene_version_note": s["version_note"],
            "scene_watch_url": s["watch_url"],
            "candidate_id": candidate_id, "candidate_status": candidate_status,
            "candidate_source": source, "confidence": confidence, "consensus": consensus,
            "proposal_kind": proposal_kind, "proposal_note": proposal_note,
            "proposed_label": start_label, "proposed_seconds": start,
            "proposed_end_label": end_label, "proposed_end_seconds": end,
            "moment_label": moment_label,
            "open_url": open_url, "open_provider": open_provider, "open_kind": open_kind,
            "open_note": open_note, "source_url": source_url,
            "local_command": local_command,
            "search_url": SERVICE_SEARCH["kinopoisk"].format(q=q),
            "youtube_search_url": SERVICE_SEARCH["youtube"].format(q=q_full),
            "imdb_url": f"https://www.imdb.com/title/{imdb}/" if imdb else None,
        })

    # Удобный порядок отсмотра: сначала то, что открывается сразу на моменте,
    # затем реальные SRT-кандидаты, затем черновики. Внутри группы — по названию.
    items.sort(key=lambda i: (
        not bool(i["open_url"]),
        i["proposal_kind"] != "srt",
        i["movie_title"].casefold(),
    ))

    stats = {
        "total": len(items),
        "verified": sum(1 for i in items if i["status"] == "verified"),
        "pending": sum(1 for i in items if i["status"] == "pending"),
        "draft": sum(1 for i in items if i["status"] == "draft"),
        "with_real_timecode": sum(1 for i in items if i["proposal_kind"] == "srt"),
        "with_draft_timecode": sum(1 for i in items if i["proposal_kind"] == "draft"),
        "with_open_url": sum(1 for i in items if i["open_url"]),
        "with_local_command": sum(1 for i in items if i["local_command"]),
    }
    return {"items": items, "stats": stats}
