#!/usr/bin/env python3
"""Автономная библиотека таймкодов: извлекает КАНДИДАТОВ таймкодов из внешних субтитров.

Источники (см. docs/06-timecode-sources.md): OpenSubtitles/OPUS (прод), GitHub-зеркала .srt (dev),
локальный каталог .srt + manifest.json.

АНТИ-МУСОР (почему таймкоды не пустые и осмысленные):
  1. СЕМАНТИКА: для RU-субтитров (lang=ru, напр. пары из OPUS) матчим НАШЕ описание сцены
     (description+why_fits, русский) с русскими строками субтитров через TF-IDF косинус —
     таймкод попадает туда, где СМЫСЛ совпадает, а не просто ключевые слова.
     Для EN-файлов — keyword-группы из manifest (semantic cross-lingual без внешних эмбеддингов невозможен).
  2. КОНСЕНСУС: несколько независимых файлов субтитров одного фильма (в manifest одним блоком)
     должны сойтись на одном моменте (±CONSENSUS_WINDOW сек) — consensus растёт, confidence растёт.
  3. ПОРОГ: auto=1 только при consensus>=2 и confidence>=--auto-threshold; иначе — очередь ручной проверки.

ЖЁСТКОЕ ПРАВИЛО: текст субтитров/реплик НЕ храним и не показываем — только факт таймкода,
sha1 совпавшей строки, источник и лицензия. «Смысл реплик» клиент видит через НАШИ description/why_fits
и moment_label (метка админа нашими словами при approve).

Запуск:
  .venv/bin/python scripts/fetch_timecodes.py --manifest data/subtitles_samples/manifest.json
  ... --dry-run | --auto-threshold 0.45
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config  # noqa: E402

SRT_RE = re.compile(
    r"(\d+)\s*\r?\n(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*\r?\n(.*?)(?=\r?\n\r?\n|\Z)",
    re.S)
MIN_CLIP = 25.0   # мин. длительность отрезка сцены, сек
MAX_CLIP = 75.0   # макс. длительность отрезка, сек
TAG_RE = re.compile(r"<[^>]+>")
NONWORD_RE = re.compile(r"[^a-z0-9а-яё\s]", re.I)
STOP = set(("the a an and or but if then of to in on at for with from by is are was were be been it its "
            "this that these those you your i my me we they he she his her as not no so do does did have has "
            "had will would can could should there here what when where why how all any both each other more "
            "most some such only own same too very just").split())
STOP_RU = set(("и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее "
               "мне было вот от меня еще нет о из ему теперь когда даже ну ли если уже или ни быть был него "
               "до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для "
               "мы тебя их чем была сам под будет ж тогда кто этот того потому этого какой совсем ним здесь "
               "этом один почти мой чтобы нее сейчас куда зачем всех никогда можно при наконец два об хоть "
               "после над больше тот через эти нас про всего них какая много разве три эту мою впрочем хорошо "
               "свою этой перед иногда лучше чуть том нельзя такой им более всегда").split())

CONSENSUS_WINDOW = 10.0  # сек


def parse_srt(path: str):
    """-> [(line_index, start_seconds, end_seconds, raw_text)] — у каждого блока субтитров есть начало И конец."""
    with open(path, encoding="utf-8", errors="replace") as f:
        data = f.read().replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for m in SRT_RE.finditer(data):
        idx = int(m.group(1))
        start = int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4)) + int(m.group(5)) / 1000.0
        end = int(m.group(6)) * 3600 + int(m.group(7)) * 60 + int(m.group(8)) + int(m.group(9)) / 1000.0
        text = m.group(10).strip()
        if text:
            out.append((idx, start, end, text))
    return out


def clip_window(blocks, picked_idx, start):
    """Отрезок сцены: начинаем с совпавшего блока и доклеиваем следующие, пока клип
    не станет длительностью [MIN_CLIP, MAX_CLIP] — так на открытке не точка, а сцена целиком."""
    by_idx = {b[0]: b for b in blocks}
    end = by_idx.get(picked_idx, (None, start, start, ""))[2]
    i = picked_idx
    while end - start < MIN_CLIP and i + 1 in by_idx and end - start < MAX_CLIP:
        i += 1
        end = max(end, by_idx[i][2])
    return end


def _stem_ru(w: str) -> str:
    """Грубый стемминг для RU: извиняюсь/извиняется -> извиня (справляется с морфологией без внешних библиотек)."""
    return w[:5] if len(w) >= 6 else w


def norm(text: str, lang: str = "en") -> str:
    t = TAG_RE.sub(" ", text.lower())
    t = NONWORD_RE.sub(" ", t)
    stop = STOP_RU if lang == "ru" else STOP
    words = [w for w in t.split() if w and w not in stop]
    if lang == "ru":
        words = [_stem_ru(w) for w in words]
    return " ".join(words)


def fmt_tc(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def keyword_moments(blocks, keywords, lang="en", top_n=3, min_gap=60):
    """Keyword-матчинг (для EN-файлов): топ-N строк по совпадению с ключевыми словами."""
    kws = [k.lower() for k in keywords if norm(k, lang)]
    scored = []
    for idx, start, end, text in blocks:
        toks = set(norm(text, lang).split())
        if not toks:
            continue
        hits = sum(1 for k in kws if k in toks or any(k in t for t in toks if len(k) > 3))
        if hits:
            scored.append((hits / max(1, len(toks)) * 0.5 + hits * 0.5, idx, start, end, text))
    scored.sort(reverse=True)
    picked = []
    for score, idx, start, end, text in scored:
        if all(abs(start - p["start"]) >= min_gap for p in picked):
            picked.append({"score": score, "line": idx, "start": start, "end": clip_window(blocks, idx, start), "text": text})
        if len(picked) >= top_n:
            break
    return picked


def semantic_moments(blocks, ru_query, lang="ru", top_n=3, min_gap=60, min_score=0.08):
    """Семантический матчинг (для RU-файлов): TF-IDF косинус между нашим описанием сцены
    и строками субтитров на том же языке. Текст строк используется ТОЛЬКО на входе —
    наружу уходит лишь время + sha1."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    normed = [(b, norm(b[3], lang)) for b in blocks]  # (блок, нормализованный текст), индексы не теряем
    normed = [(b, t) for b, t in normed if t]
    if not normed:
        return []
    vec = TfidfVectorizer(ngram_range=(1, 2))
    mat = vec.fit_transform([t for _, t in normed] + [norm(ru_query, "ru")])
    q = mat[-1]
    sims = cosine_similarity(q, mat[:-1]).flatten()
    order = sims.argsort()[::-1]
    picked = []
    for i in order:
        if sims[i] < min_score:
            break
        line, start, _end, text = normed[i][0]
        if all(abs(start - p["start"]) >= min_gap for p in picked):
            picked.append({"score": float(sims[i]), "line": line, "start": start,
                           "end": clip_window(blocks, line, start), "text": text})
        if len(picked) >= top_n:
            break
    return picked


def cluster_consensus(per_file):
    """per_file: [{file, source, cand:{score,line,start,text}}] -> добавляет consensus/confidence/auto.
    Моменты считаются одним, если времена в пределах CONSENSUS_WINDOW."""
    clusters = []  # {starts:[...], files:set, best:cand, source:best source}
    for entry in per_file:
        placed = False
        for c in clusters:
            if abs(c["best"]["start"] - entry["cand"]["start"]) <= CONSENSUS_WINDOW:
                c["files"].add(entry["file"])
                c["starts"].append(entry["cand"]["start"])
                if entry["cand"]["score"] > c["best"]["score"]:
                    c["best"] = entry["cand"]
                    c["source"] = entry["source"]
                    c["line"] = entry["cand"]["line"]
                placed = True
                break
        if not placed:
            clusters.append({"best": entry["cand"], "source": entry["source"], "line": entry["cand"]["line"],
                             "files": {entry["file"]}, "starts": [entry["cand"]["start"]]})
    out = []
    for c in clusters:
        consensus = len(c["files"])
        mean_start = sum(c["starts"]) / len(c["starts"])
        score = c["best"]["score"]
        confidence = score * (1 + 0.25 * (consensus - 1))
        out.append({"cand": c["best"], "source": c["source"], "line": c["line"],
                    "start": mean_start, "end": c["best"].get("end", mean_start),
                    "score": score, "consensus": consensus,
                    "confidence": round(confidence, 4)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--db", default=config.DB_PATH)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--auto-threshold", type=float, default=0.45,
                    help="confidence для auto=1 при consensus>=2")
    ap.add_argument("--min-consensus", type=int, default=2)
    ap.add_argument("--max-per-scene", type=int, default=0,
                    help="оставить только N лучших кандидатов на сцену (0 = без ограничения)")
    args = ap.parse_args()

    base = os.path.dirname(os.path.abspath(args.manifest))
    with open(args.manifest, encoding="utf-8") as f:
        data = json.load(f)
    manifest = data["entries"] if isinstance(data, dict) and "entries" in data else data

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).isoformat()
    total = 0
    for entry in manifest:
        lang = entry.get("lang", "en")
        # один movie-блок может нести несколько файлов (file может быть списком) — консенсус
        files = entry["file"] if isinstance(entry["file"], list) else [entry["file"]]
        sources = entry.get("sources") or [entry.get("source", "local")] * len(files)
        print(f"== {entry['movie_title']} ({entry['movie_year']}) [{lang}] <- файлов: {len(files)}: {', '.join(files)}")
        per_file = []
        for fn, src in zip(files, sources):
            fpath = fn if os.path.isabs(fn) else os.path.join(base, fn)
            if not os.path.exists(fpath):
                continue
            fblocks = parse_srt(fpath)
            for sc in entry.get("scenes", []):
                row = conn.execute("SELECT id, movie_title, description, why_fits FROM scenes WHERE movie_title=? AND movie_year=?",
                                   (sc["match_title"], entry["movie_year"])).fetchone()
                if not row:
                    print(f"   ?? сцена «{sc['match_title']}» ({entry['movie_year']}) не в каталоге")
                    continue
                ru_query = " ".join([row["movie_title"], row["description"] or "", row["why_fits"] or "",
                                     " ".join(sc.get("keywords_ru", []))])
                if lang == "ru":
                    cands = semantic_moments(fblocks, ru_query, lang="ru")
                else:
                    cands = keyword_moments(fblocks, sc.get("keywords", []), lang="en")
                for c in cands:
                    per_file.append({"file": fpath, "source": src, "scene": row, "cand": c,
                                     "moment_label": sc.get("moment_label")})
        # группируем по сцене и считаем консенсус
        by_scene = {}
        for e in per_file:
            by_scene.setdefault(e["scene"]["id"], []).append(e)
        for scene_id, entries in by_scene.items():
            scene = entries[0]["scene"]
            merged = cluster_consensus(entries)
            merged.sort(key=lambda x: (x["confidence"], x["score"]), reverse=True)
            if args.max_per_scene > 0:
                merged = merged[:args.max_per_scene]
            for m in merged:
                auto = 1 if (m["consensus"] >= args.min_consensus and m["confidence"] >= args.auto_threshold) else 0
                sha = hashlib.sha1(norm(m["cand"]["text"]).encode("utf-8")).hexdigest()
                label = fmt_tc(m["start"])
                if args.dry_run:
                    print(f"   [dry] «{scene['movie_title']}» ⏱ {label}–{fmt_tc(m['end'])} score={m['score']:.3f} "
                          f"consensus={m['consensus']} confidence={m['confidence']:.3f} auto={auto}")
                else:
                    # moment_label — наша короткая метка момента СВОИМИ словами (из манифеста),
                    # а не текст субтитров. Админ может уточнить её при ручной сверке.
                    moment_label = (entries[0].get("moment_label") or "").strip() or None
                    conn.execute(
                        "INSERT OR IGNORE INTO timecode_candidates"
                        " (scene_id, service, timecode_label, timecode_seconds, timecode_end_label,"
                        "  timecode_end_seconds, source, source_license,"
                        "  evidence_sha1, line_index, match_score, moment_label, confidence, consensus, auto, status, created_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)",
                        (scene_id, entry.get("service", "opensubtitles"), label, int(round(m["start"])),
                         fmt_tc(m["end"]), int(round(m["end"])),
                         m["source"], entry["source_license"], sha, m["line"], round(m["score"], 4),
                         moment_label, m["confidence"], m["consensus"], auto, now))
                total += 1
    if not args.dry_run:
        conn.commit()
    conn.close()
    print(f"Готово: кандидатов: {total} (режим: {'dry-run' if args.dry_run else 'запись в БД'}, "
          f"auto-порог confidence>={args.auto_threshold} при consensus>={args.min_consensus})")


if __name__ == "__main__":
    main()
