#!/usr/bin/env python3
"""Категорийный отбор таймкодов: не «похоже на описание», а под интент + тип фрагмента.

Читает кэшированные SRT (БЕЗ сохранения текста), режет на окна 25–75 сек,
скорит каждое окно по:
  * лексикону категории (RU/EN),
  * TF-IDF близости к intent_query,
  * структуре фрагмента (число реплик, паузы, монолог/диалог, негативные маркеры),
и пишет в `timecode_candidates` только окна выше порога. Сцены без
доказательного фрагмента НЕ получают случайного времени: остаётся `needs_curator`.

Запуск:
  .venv/bin/python scripts/import_intent_candidates.py --dry-run
  .venv/bin/python scripts/import_intent_candidates.py --write --refresh
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))

from app import config  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.intent_taxonomy import (  # noqa: E402
    category_by_id, categories, fragment_types, intent_for, load_taxonomy,
)
import fetch_timecodes as ft  # noqa: E402

LOCAL_MANIFEST = os.path.join(BASE_DIR, "data", "timecode_library", "cache", "_local_manifest.json")
MAX_WINDOWS_PER_FILE = 420


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def windows(blocks, min_clip, max_clip):
    """Окна 25–75 сек, начинающиеся с реплики. Возвращает только структуру,
    а не текст — текст живёт лишь внутри этой функции."""
    by_idx = {b[0]: b for b in blocks}
    if not blocks:
        return []
    total_end = max(b[2] for b in blocks)
    step = max(1, len(blocks) // MAX_WINDOWS_PER_FILE)
    out = []
    for pos in range(0, len(blocks), step):
        idx, start, end, text = blocks[pos]
        if start < 120 or start > total_end - 150:
            continue
        j = idx
        parts = [text]
        win_end = end
        prev_end = end
        gaps = []
        while win_end - start < min_clip and j + 1 in by_idx and win_end - start < max_clip:
            j += 1
            b = by_idx[j]
            gaps.append(max(0.0, b[1] - prev_end))
            parts.append(b[3])
            win_end = max(win_end, b[2])
            prev_end = win_end
        if win_end - start < min_clip or win_end - start > max_clip + 2:
            continue
        full = " ".join(parts)
        out.append({
            "line": idx, "start": start, "end": win_end, "text": full,
            "lines": len(parts), "duration": win_end - start,
            "max_gap": max(gaps) if gaps else 0.0,
            "avg_chars": sum(len(p) for p in parts) / max(1, len(parts)),
        })
    return out


def score_windows(wins, cat, intent, lang):
    """Проставляет score/tfidf/hits/fragment_type. Текст не покидает функцию."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    if not wins:
        return []
    tax = load_taxonomy()
    negatives = tax["negative_markers"]
    lex = list(cat["lexicon_ru"] + cat["lexicon_en"])
    query_ru = intent.get("intent_query_ru", cat["query_ru"])
    query_en = intent.get("intent_query_en", cat["query_en"])
    query = query_ru if lang == "ru" else query_en
    query = (query + " " + " ".join(lex)).strip()

    normed = [ft.norm(w["text"], "ru" if lang == "ru" else "en") for w in wins]
    try:
        vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1, token_pattern=r"(?u)\b\w+\b")
        mat = vec.fit_transform(normed + [ft.norm(query, "ru" if lang == "ru" else "en")])
        sims = cosine_similarity(mat[-1], mat[:-1]).flatten()
    except ValueError:
        sims = [0.0] * len(wins)

    expected = intent.get("fragment_type", "dialogue")
    for i, w in enumerate(wins):
        low = w["text"].lower()
        hits = [t for t in lex if t in low]
        neg = sum(1 for m in negatives if m in low)
        tfidf = float(sims[i])
        lex_score = min(1.0, len(hits) / 3.0)
        struct = 0.55
        if 25 <= w["duration"] <= 75 and w["lines"] >= 2 and w["max_gap"] <= 5:
            struct += 0.25
        if w["lines"] >= 4:
            struct += 0.10
        if expected == "monologue" and w["lines"] <= 3 and w["avg_chars"] > 50:
            struct += 0.10
        if expected == "dialogue" and w["lines"] >= 4:
            struct += 0.10
        struct -= 0.45 * min(1, neg)
        struct = max(0.0, min(1.0, struct))
        w["score"] = round(0.45 * lex_score + 0.30 * tfidf + 0.25 * struct, 4)
        w["tfidf"] = round(tfidf, 4)
        w["hits"] = len(hits)
        w["negative"] = neg
        w["fragment_type"] = (
            "song_music" if neg and any(m in low for m in ("♪", "♫", "music playing"))
            else "monologue" if w["lines"] <= 3 and w["avg_chars"] > 55
            else "dialogue"
        )
        w["features"] = {
            "lexicon_hits": len(hits), "tfidf": round(tfidf, 4),
            "lines": w["lines"], "duration": round(w["duration"], 1),
            "max_gap": round(w["max_gap"], 1), "avg_chars": round(w["avg_chars"], 1),
            "negative_markers": neg, "expected_fragment": expected,
            "actual_fragment": w["fragment_type"],
        }
    return wins


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=config.DB_PATH)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="удалить прошлые intent-кандидаты перед записью")
    ap.add_argument("--archive-old", action="store_true", help="старые pending без category перевести в archived")
    ap.add_argument("--threshold", type=float, default=load_taxonomy()["gate_threshold"])
    ap.add_argument("--max-per-scene", type=int, default=3)
    args = ap.parse_args()
    if not args.dry_run and not args.write:
        ap.error("нужен --dry-run или --write")

    init_db(args.db)
    tax = load_taxonomy()
    min_clip = tax["min_clip_seconds"]
    max_clip = tax["max_clip_seconds"]
    min_gap = tax["min_gap_between_candidates_seconds"]
    min_hits = int(tax.get("min_lexicon_hits", 2))
    with open(LOCAL_MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)

    conn = connect(args.db)
    if args.write and args.refresh:
        conn.execute("DELETE FROM timecode_candidates WHERE category IS NOT NULL")
    if args.write and args.archive_old:
        conn.execute(
            "UPDATE timecode_candidates SET status='archived' "
            "WHERE category IS NULL AND status='pending'"
        )

    rows = []
    scenes_seen = set()
    for entry in manifest["entries"]:
        lang = entry.get("lang", "en")
        for sc in entry.get("scenes", []):
            title = sc.get("match_title")
            year = entry.get("movie_year")
            scene = conn.execute(
                "SELECT id FROM scenes WHERE movie_title=? AND movie_year=? AND status='approved'",
                (title, year),
            ).fetchone()
            if not scene:
                continue
            intent = intent_for(title, year)
            cat = category_by_id(intent["primary_intent"])
            if not cat:
                continue
            scenes_seen.add((title, year))
            per_file = []
            for file_path, source_url in zip(entry["file"], entry["sources"]):
                if not file_path.endswith(".srt") or not os.path.exists(file_path):
                    continue
                blocks = ft.parse_srt(file_path)
                wins = windows(blocks, min_clip, max_clip)
                wins = score_windows(wins, cat, intent, lang)
                for w in wins:
                    if w["score"] >= args.threshold and w["hits"] >= min_hits:
                        w["file"] = file_path
                        w["source"] = source_url
                        per_file.append(w)
            # минимум 60 сек между кандидатами, топ по скору
            per_file.sort(key=lambda w: -w["score"])
            picked = []
            for w in per_file:
                if all(abs(w["start"] - p["start"]) >= min_gap for p in picked):
                    picked.append(w)
                if len(picked) >= args.max_per_scene:
                    break
            for w in picked:
                consensus = sum(1 for x in per_file if abs(x["start"] - w["start"]) <= 10 and x["file"] != w["file"])
                consensus = max(1, consensus + 1)
                label = f"{cat['label']}: {w['hits']} смысловых совпадения, {fragment_types().get(w['fragment_type'], w['fragment_type'])}, {int(w['duration'])} сек, TF-IDF {w['tfidf']:.2f}, консенсус {consensus}"
                rows.append({
                    "scene_id": scene["id"], "title": title, "year": year,
                    "category": cat["id"], "fragment_type": w["fragment_type"],
                    "start": w["start"], "end": w["end"], "line": w["line"],
                    "score": w["score"], "tfidf": w["tfidf"],
                    "features": w["features"], "reason": label,
                    "sha1": hashlib.sha1(ft.norm(w["text"], lang).encode("utf-8")).hexdigest(),
                    "source": w["source"], "license": entry.get("source_license", ""),
                    "consensus": consensus, "anchor": intent.get("curated_anchor", "")[:180],
                })

    scenes_with = len({(r["title"], r["year"]) for r in rows})
    if args.dry_run:
        print(f"DRY-RUN: сцен с SRT {len(scenes_seen)}, сцен с доказательным фрагментом {scenes_with}, "
              f"кандидатов выше порога {args.threshold} и hits>={min_hits}: {len(rows)}")
        for r in rows[:25]:
            print(f"  {r['title']} ({r['year']}) {ft.fmt_tc(r['start'])}–{ft.fmt_tc(r['end'])} "
                  f"{r['category']}/{r['fragment_type']} score={r['score']:.3f} · {r['reason'][:90]}")
        conn.close()
        return

    inserted = 0
    for r in rows:
        cur = conn.execute(
            "INSERT OR IGNORE INTO timecode_candidates "
            "(scene_id, service, timecode_label, timecode_seconds, timecode_end_label, timecode_end_seconds,"
            " source, source_license, evidence_sha1, line_index, match_score, moment_label, confidence,"
            " consensus, auto, category, fragment_type, evidence_features, reason, intent_score, status,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)",
            (r["scene_id"], "intent", ft.fmt_tc(r["start"]), int(r["start"]),
             ft.fmt_tc(r["end"]), int(r["end"]), r["source"], r["license"], r["sha1"],
             r["line"], r["tfidf"], r["anchor"], r["score"], r["consensus"], 0,
             r["category"], r["fragment_type"], json.dumps(r["features"], ensure_ascii=False),
             r["reason"], r["score"], now_iso()),
        )
        inserted += cur.rowcount
    conn.commit()
    conn.close()
    print(f"Готово: сцен с SRT {len(scenes_seen)}, сцен с доказательным фрагментом {scenes_with}, "
          f"candidates {len(rows)}, вставлено {inserted}, порог {args.threshold}, "
          f"archived-old={'да' if args.archive_old else 'нет'}")


if __name__ == "__main__":
    main()
