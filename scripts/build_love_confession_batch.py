#!/usr/bin/env python3
"""Блок модерации №1: 50 отрывков «признание в любви и чувства».

Берёт локальные SRT-кэши там, где они есть: находит строки с прямыми/
трогательными признаниями и строит окно 25–75 секунд вокруг такой строки.
Для фильмов без надёжного SRT даёт уникальный непроверенный начальный
ориентир по каталогу — не «случайную минуту» и не claimed timecode. Текст
субтитров используется только внутри функции; наружу уходят время, sha1,
источник, лицензия, контекст каталога и метки куратора. verified=1 НЕ
ставится, version_note обязателен при подтверждении.

    .venv/bin/python scripts/build_love_confession_batch.py --dry-run
    .venv/bin/python scripts/build_love_confession_batch.py --write

`--write` удаляет прошлую версию этого блока и создаёт 50 `pending`-кандидатов
с `batch_id='love_confession_50'`. Основная очередь 146 сцен не меняется.
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
from app.intent_taxonomy import intent_for, fragment_types, starting_timecode  # noqa: E402
import fetch_timecodes as ft  # noqa: E402

LIB = os.path.join(BASE_DIR, "data", "timecode_library")
MANIFEST = os.path.join(LIB, "cache", "_local_manifest.json")
OUT_JSON = os.path.join(LIB, "love_confession_batch.json")
OUT_DOC = os.path.join(BASE_DIR, "docs", "20-love-confession-batch.md")
CURATED_JSON = os.path.join(LIB, "love_confession_curated.json")
BATCH_ID = "love_confession_50"
BATCH_LABEL = "Признание в любви и чувства"

STRONG_RU = [
    "я люблю тебя", "я тебя люблю", "люблю тебя", "ты мне нужен", "ты мне нужна",
    "останься", "не могу без", "признаюсь", "выходи за меня", "поцелуй меня",
    "я скучал", "я скучала", "навсегда", "моё сердце", "мое сердце", "любишь меня",
]
STRONG_EN = [
    "i love you", "i am in love with you", "i need you", "stay with me", "marry me",
    "kiss me", "i care about you", "forever", "my heart", "missed you", "come back",
    "hold me", "i do", "i will always", "i never stopped", "love of my life",
]
LEX_RU = [
    "люблю", "любить", "любовь", "люби", "любим", "влюблён", "влюблена", "признаюсь",
    "признание", "чувств", "чувствую", "сердце", "поцелуй", "останься", "не могу без",
    "ты мне", "нужен", "нужна", "дорогой", "дорогая", "милый", "милая", "навсегда",
    "всегда", "нежность", "нежно", "счастье", "счастлив", "прости", "скучал", "вернись",
    "женюсь", "замуж", "поцелую", "обниму", "письмо", "прощай",
]
LEX_EN = [
    "love", "loved", "lover", "loving", "feelings", "confess", "admit", "need you",
    "stay with me", "my heart", "marry me", "kiss me", "i care about you", "forever",
    "always", "darling", "dear", "sweetheart", "tender", "missed you", "come back",
    "forgive me", "happy", "hold me", "be with me", "i do", "letter", "goodbye",
]
NEG_MARKERS = ["♪", "♫", "music playing", "http", "www.", "subtitles", "[ music ", "% "]

ROMANTIC_WORDS = [
    "люб", "признан", "чувств", "сердц", "поцел", "свадьб", "роман", "нежност",
    "невест", "замуж", "влюб", "прощан", "письм", "памят", "мечт", "надежд",
]
INTENT_BOOST = {
    "love_confession": 1.20,
    "anniversary_long_love": 0.95,
    "quiet_farewell": 0.90,
    "apology_reconciliation": 0.85,
    "support_hope": 0.55,
    "nostalgia_classic": 0.45,
    "inspiration_dream": 0.35,
    "female_friendship": 0.30,
    "family_parents": 0.30,
    "children_animation": 0.20,
}

# Кураторские подписи и внешние подсказки для самых узнаваемых сцен.
# Это НАШИ описания, не текст субтитров и не «проверенный таймкод».
with open(CURATED_JSON, encoding="utf-8") as _f:
    CURATED_MOMENTS = json.load(_f)["moments"]

ICONIC = {}
for _key, _v in CURATED_MOMENTS.items():
    _title = _key.rsplit("|", 1)[0]
    ICONIC[_title] = {
        "labels": [_v["moment"], _v["moment"], _v["moment"]],
        "context": " ".join(filter(None, [_v["context"], _v["external_note"]])),
        "tags": list(dict.fromkeys(_v["tags"] + ["подготовленный кандидат"])),
        "refs": _v["refs"],
        "external_note": _v["external_note"],
        "moment": _v["moment"],
        "external_time": _v.get("external_time", ""),
    }

# 50 разных фильмов: только романтика/чувства. Порядок задаёт curated-файл.
ROMANTIC_KEYS = list(CURATED_MOMENTS.keys())

EXTERNAL_NOTES = [
    "Clip.Cafe, подборки цитат и обзоры romantic scenes использованы только как внешние подсказки к узнаваемым эпизодам.",
    "Локальные SRT дают окно 25–75 секунд с sha1 и номером строки; начальные ориентиры дают уникальный sha1 каталога, но не номер строки SRT.",
    "Все 50 карточек — 50 разных фильмов; verified_by_human=false, version_note обязателен.",
]


def parse_tc(value: str) -> int:
    h, m, sec = (int(x) for x in value.split(":"))
    return h * 3600 + m * 60 + sec


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def context_score(scene, intent):
    text = " ".join(str(scene[k] or "") for k in ("description", "why_fits", "curated_anchor")).lower()
    hits = sum(1 for w in ROMANTIC_WORDS if w in text)
    return round(hits * 0.12 + INTENT_BOOST.get(intent.get("primary_intent", ""), 0.0), 4)


def emotion_tags(text, intent, iconic):
    if iconic and iconic.get("tags"):
        return list(dict.fromkeys(iconic["tags"]))
    tags = []
    low = text.lower()
    mapping = [
        (("i love you", "люблю тебя", "я люблю"), "прямое признание"),
        (("missed", "скучал", "скучала"), "разлука"),
        (("stay", "останься", "come back", "вернись"), "просьба остаться"),
        (("forever", "always", "навсегда", "всегда"), "обещание"),
        (("forgive", "прости", "прощай", "goodbye"), "прощание"),
        (("letter", "письмо"), "письмо"),
        (("heart", "сердце"), "сердце"),
        (("darling", "милый", "дорогая", "dear"), "нежность"),
    ]
    for needles, tag in mapping:
        if any(n in low for n in needles):
            tags.append(tag)
    cat = intent.get("primary_intent")
    if cat == "anniversary_long_love":
        tags.append("долгая любовь")
    elif cat == "quiet_farewell":
        tags.append("светлая грусть")
    elif cat == "support_hope":
        tags.append("поддержка")
    return list(dict.fromkeys(tags)) or ["трогательно", "чувства"]


def centered_windows(blocks, lang):
    """Окна вокруг строки с прямым признанием. Текст не покидает функцию."""
    strong = STRONG_RU if lang == "ru" else STRONG_EN
    lex = LEX_RU if lang == "ru" else LEX_EN
    by_idx = {b[0]: b for b in blocks}
    if not blocks:
        return []
    total_end = max(b[2] for b in blocks)
    out = []
    for idx, st, en, text in blocks:
        low = text.lower()
        if not any(p in low for p in strong):
            continue
        pre = [b for b in blocks if b[1] <= st - 10]
        start_b = pre[-1] if pre else blocks[0]
        if st - start_b[1] > 45:
            start_b = max((b for b in blocks if b[1] <= st - 5), key=lambda b: b[1], default=blocks[0])
        post = [b for b in blocks if b[2] >= min(total_end, st + 35)]
        end_b = post[0] if post else blocks[-1]
        if end_b[2] - start_b[1] > 75:
            end_b = min((b for b in blocks if b[2] >= min(total_end, st + 25)),
                        key=lambda b: b[2], default=end_b)
        dur = end_b[2] - start_b[1]
        if dur < 25 or dur > 75:
            continue
        parts = [b[3] for b in blocks if start_b[0] <= b[0] <= end_b[0]]
        window = " ".join(parts)
        wl = window.lower()
        str_hits = sum(1 for p in strong if p in wl)
        hits = sum(1 for t in lex if t in wl)
        neg = sum(1 for m in NEG_MARKERS if m in wl)
        if str_hits < 1 or neg >= 2:
            continue
        score = round(0.55 * min(3, str_hits) + 0.12 * hits - 0.20 * neg
                      + 0.04 * len(parts) + 0.04 * max(0, 5 - max(0, st - start_b[1])), 4)
        out.append({
            "line": idx, "start": start_b[1], "end": end_b[2], "duration": dur,
            "strong_hits": str_hits, "lexicon_hits": hits, "negative_markers": neg,
            "lines": len(parts), "score": score,
            "sha1": hashlib.sha1(ft.norm(window, lang).encode("utf-8")).hexdigest(),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=config.DB_PATH)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--max-per-scene", type=int, default=2)
    args = ap.parse_args()
    if not args.dry_run and not args.write:
        ap.error("нужен --dry-run или --write")

    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    conn = connect(args.db)
    scenes = {
        (r["movie_title"], r["movie_year"]): dict(r)
        for r in conn.execute("SELECT * FROM scenes WHERE status='approved'").fetchall()
    }
    conn.close()

    candidates = []
    for e in manifest["entries"]:
        lang = e.get("lang", "en")
        for sc in e.get("scenes", []):
            title = sc.get("match_title")
            year = e.get("movie_year")
            scene = scenes.get((title, year))
            if not scene:
                continue
            intent = intent_for(title, year)
            ctx = context_score(scene, intent)
            iconic = ICONIC.get(title)
            per_file = []
            for fp, src in zip(e["file"], e["sources"]):
                if not fp.endswith(".srt") or not os.path.exists(fp):
                    continue
                for w in centered_windows(ft.parse_srt(fp), lang):
                    per_file.append({**w, "source": src, "file": fp})
            # консенсус по нескольким SRT-файлам одного фильма (±10 сек)
            for w in per_file:
                w["consensus"] = max(1, sum(
                    1 for x in per_file if x["file"] != w["file"] and abs(x["start"] - w["start"]) <= 10
                ) + 1)
                w["final"] = round(w["score"] + ctx + 0.10 * (w["consensus"] - 1), 4)
            per_file.sort(key=lambda w: -w["final"])
            picked = []
            for w in per_file:
                if all(abs(w["start"] - p["start"]) >= 60 for p in picked):
                    picked.append(w)
                if len(picked) >= args.max_per_scene:
                    break
            for n, w in enumerate(picked):
                label = (iconic["labels"][n] if iconic and len(iconic["labels"]) > n
                         else f"{intent.get('curated_anchor', 'Эпизод с признанием')[:100]} · фрагмент {n + 1}")
                context = (iconic["context"] if iconic else
                           " ".join(filter(None, [scene.get("description"), scene.get("why_fits"),
                                                  intent.get("curated_anchor")]))[:240])
                candidates.append({
                    "key": f"{title}|{year}", "movie_title": title, "movie_year": year,
                    "imdb_id": scene.get("imdb_id"), "genre": scene.get("genre"),
                    "description": scene.get("description"), "why_fits": scene.get("why_fits"),
                    "category": "love_confession", "category_label": BATCH_LABEL,
                    "primary_intent": intent.get("primary_intent"),
                    "secondary_intents": intent.get("secondary_intents", []),
                    "fragment_type": intent.get("fragment_type", "dialogue"),
                    "fragment_type_label": fragment_types().get(intent.get("fragment_type", "dialogue"), ""),
                    "curated_anchor": intent.get("curated_anchor", ""),
                    "moment_label": label, "context": context,
                    "emotion_tags": emotion_tags("", intent, iconic),
                    "timecode_label": ft.fmt_tc(w["start"]),
                    "timecode_seconds": int(round(w["start"])),
                    "timecode_end_label": ft.fmt_tc(w["end"]),
                    "timecode_end_seconds": int(round(w["end"])),
                    "duration_seconds": round(w["duration"], 1),
                    "source": f"{w['source']}#{BATCH_ID}",
                    "evidence_source": w["source"],
                    "source_license": e.get("source_license", ""),
                    "evidence_sha1": w["sha1"], "line_index": w["line"],
                    "match_score": round(w["score"] / 4, 4),
                    "intent_score": w["final"], "confidence": w["final"],
                    "consensus": w["consensus"], "auto": 0,
                    "status": "pending", "verified": 0,
                    "version_hint": "Сверь издание/реставрацию/серию: таймкод может сдвинуться. version_note обязателен.",
                    "external_refs": (iconic or {}).get("refs", []),
                    "evidence_kind": "srt_window",
                    "evidence_note": "Окно 25–75 секунд вокруг строки локального SRT; sha1 и номер строки сохранены, текст — нет.",
                    "external_note": (iconic or {}).get("external_note", ""),
                    "curated_orienteer": False,
                    "evidence_features": {
                        "batch_id": BATCH_ID, "centered_on": "love/feelings phrase",
                        "strong_hits": w["strong_hits"], "lexicon_hits": w["lexicon_hits"],
                        "negative_markers": w["negative_markers"], "lines": w["lines"],
                        "duration_seconds": round(w["duration"], 1), "context_score": ctx,
                        "emotion_tags": emotion_tags("", intent, iconic), "context": context,
                        "moment_label": label, "version_hint": "Сверь издание/реставрацию/серию: таймкод может сдвинуться. version_note обязателен.",
                        "external_refs": (iconic or {}).get("refs", []),
                        "primary_intent": intent.get("primary_intent"),
                        "secondary_intents": intent.get("secondary_intents", []),
                        "curated_anchor": intent.get("curated_anchor", ""),
                        "category_label": BATCH_LABEL, "genre": scene.get("genre"),
                        "description": scene.get("description"), "why_fits": scene.get("why_fits"),
                        "imdb_id": scene.get("imdb_id"),
                        "evidence_kind": "srt_window",
                        "evidence_note": "Окно 25–75 секунд вокруг строки локального SRT; sha1 и номер строки сохранены, текст — нет.",
                        "external_note": (iconic or {}).get("external_note", ""),
                        "curated_orienteer": False,
                    },
                })

    # Берём РОВНО ОДИН лучший отрывок на фильм, чтобы получить 50 разных картин.
    best_by_scene = {}
    for c in candidates:
        k = (c["movie_title"], c["movie_year"])
        if k not in best_by_scene or c["intent_score"] > best_by_scene[k]["intent_score"]:
            best_by_scene[k] = c
    entries = []
    seen: set[tuple[str, int]] = set()
    for key in ROMANTIC_KEYS:
        title_s, year_s = key.rsplit("|", 1)
        title, year = title_s, int(year_s)
        scene = scenes.get((title, year))
        if not scene:
            continue
        c = best_by_scene.get((title, year))
        if c is None:
            intent = intent_for(title, year)
            iconic = ICONIC.get(title)
            label = (iconic["labels"][0] if iconic else
                     f"{intent.get('curated_anchor', 'Романтический эпизод')[:100]} · начальный ориентир")
            context = (iconic["context"] if iconic else
                       " ".join(filter(None, [scene.get("description"), scene.get("why_fits"),
                                              intent.get("curated_anchor")]))[:240])
            external_time = (iconic or {}).get("external_time", "")
            if external_time:
                start = parse_tc(external_time)
                start_label = ft.fmt_tc(start)
                evidence_kind = "external_orienteer"
                evidence_note = ("Внешний ориентир Clip.Cafe по узнаваемой сцене; минута не проверена"
                                 " и не является строкой SRT.")
                evidence_source = ((iconic or {}).get("refs") or ["Clip.Cafe"])[0]
                source_value = f"external-orienteer|{BATCH_ID}"
                source_license = ("Clip.Cafe: публичная страница сцены; только время и контекст,"
                                  " без видео и текста субтитров")
                version_hint = ("Внешний ориентир, НЕ проверенный момент. Сверь издание,"
                                " поправь минуты; version_note обязателен.")
                extra_tags = ["внешний ориентир", "не проверено"]
            else:
                start_label, start = starting_timecode(title, year, "love_confession", scene.get("genre"))
                evidence_kind = "starting_orienteer"
                evidence_note = ("Уникальный начальный ориентир по фильму/категории."
                                 " Не проверенный момент и не строка SRT.")
                evidence_source = "data/timecode_library/scene_intents.json"
                source_value = f"catalog-orienteer|{BATCH_ID}"
                source_license = "Каталог сцен; уникальный непроверенный ориентир, без текста субтитров"
                version_hint = ("Начальный ориентир, НЕ проверенный момент. Сверь издание,"
                                " поправь минуты; version_note обязателен.")
                extra_tags = ["начальный ориентир", "не проверено"]
            end = start + 35
            digest = hashlib.sha1(
                f"{BATCH_ID}|{title}|{year}|{start}|{evidence_kind}".encode("utf-8")).hexdigest()
            c = {
                "key": key, "movie_title": title, "movie_year": year,
                "imdb_id": scene.get("imdb_id"), "genre": scene.get("genre"),
                "description": scene.get("description"), "why_fits": scene.get("why_fits"),
                "category": "love_confession", "category_label": BATCH_LABEL,
                "primary_intent": intent.get("primary_intent"),
                "secondary_intents": intent.get("secondary_intents", []),
                "fragment_type": intent.get("fragment_type", "dialogue"),
                "fragment_type_label": fragment_types().get(intent.get("fragment_type", "dialogue"), ""),
                "curated_anchor": intent.get("curated_anchor", ""),
                "moment_label": label, "context": context,
                "emotion_tags": emotion_tags("", intent, iconic) + extra_tags,
                "timecode_label": start_label, "timecode_seconds": start,
                "timecode_end_label": ft.fmt_tc(end), "timecode_end_seconds": end,
                "duration_seconds": 35,
                "source": source_value,
                "evidence_source": evidence_source,
                "source_license": source_license,
                "evidence_sha1": digest, "line_index": 0,
                "match_score": 0.0, "intent_score": 0.0, "confidence": 0.0,
                "consensus": 1, "auto": 0, "status": "pending", "verified": 0,
                "version_hint": version_hint,
                "external_refs": (iconic or {}).get("refs", []),
                "evidence_kind": evidence_kind,
                "evidence_note": evidence_note,
                "external_note": (iconic or {}).get("external_note", ""),
                "external_time": external_time,
                "curated_orienteer": True,
                "evidence_features": {
                    "batch_id": BATCH_ID, "centered_on": evidence_kind,
                    "curated_orienteer": True, "strong_hits": 0, "lexicon_hits": 0,
                    "negative_markers": 0, "lines": 0, "duration_seconds": 35,
                    "context_score": context_score(scene, intent),
                    "emotion_tags": emotion_tags("", intent, iconic) + extra_tags,
                    "context": context, "moment_label": label,
                    "version_hint": "Начальный ориентир, НЕ проверенный момент. Сверь издание, поправь минуты; version_note обязателен.",
                    "external_refs": (iconic or {}).get("refs", []),
                    "primary_intent": intent.get("primary_intent"),
                    "secondary_intents": intent.get("secondary_intents", []),
                    "curated_anchor": intent.get("curated_anchor", ""),
                    "category_label": BATCH_LABEL, "genre": scene.get("genre"),
                    "description": scene.get("description"), "why_fits": scene.get("why_fits"),
                    "imdb_id": scene.get("imdb_id"),
                    "evidence_kind": evidence_kind,
                    "evidence_note": evidence_note,
                    "external_note": (iconic or {}).get("external_note", ""),
                    "external_time": external_time,
                },
            }
        c = dict(c)
        c["batch_rank"] = len(entries) + 1
        c["batch_id"] = BATCH_ID
        entries.append(c)
        seen.add((title, year))
        if len(entries) >= args.count:
            break
    if len(entries) < args.count:
        raise SystemExit(f"Не удалось собрать {args.count}: найдено {len(entries)}")

    data = {
        "version": 1,
        "batch_id": BATCH_ID,
        "label": BATCH_LABEL,
        "generated_by": "scripts/build_love_confession_batch.py",
        "generated_at": now_iso(),
        "comment": "50 подготовленных кандидатов из 50 разных фильмов: окна по локальным SRT и уникальные непроверенные начальные ориентиры. Текст субтитров НЕ сохранён; только время, sha1, источник, лицензия и кураторский контекст.",
        "rules": {
            "verified_by_human": False,
            "verified": 0,
            "requires_version_check": True,
            "version_note_required": True,
            "status": "pending",
            "clip_seconds": [25, 75],
            "min_gap_between_candidates_seconds": 60,
            "max_per_scene": 1,
            "evidence_modes": ["srt_window", "external_orienteer", "starting_orienteer"],
        },
        "stats": {
            "count": len(entries),
            "unique_scenes": len(seen),
            "srt_evidence": sum(1 for c in entries if c["evidence_kind"] == "srt_window"),
            "starting_orienteers": sum(1 for c in entries if c["evidence_kind"] in ("starting_orienteer", "external_orienteer")),
            "external_orienteers": sum(1 for c in entries if c["evidence_kind"] == "external_orienteer"),
            "primary_love_confession": sum(1 for c in entries if c["primary_intent"] == "love_confession"),
            "with_consensus_gt_1": sum(1 for c in entries if c["consensus"] > 1),
        },
        "external_notes": EXTERNAL_NOTES,
        "entries": entries,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _write_doc(data)

    if args.dry_run:
        print(f"DRY-RUN: {len(entries)} отрывков / {len(seen)} фильмов; JSON записан, БД не изменена")
        for c in entries[:12]:
            print(f"  {c['batch_rank']:02d} {c['movie_title']} ({c['movie_year']}) "
                  f"{c['timecode_label']}–{c['timecode_end_label']} · {c['moment_label'][:70]}")
        return

    init_db(args.db)
    conn = connect(args.db)
    conn.execute("DELETE FROM timecode_candidates WHERE batch_id=?", (BATCH_ID,))
    for c in entries:
        conn.execute(
            "INSERT INTO timecode_candidates (scene_id, service, timecode_label, timecode_seconds,"
            " timecode_end_label, timecode_end_seconds, source, source_license, evidence_sha1,"
            " line_index, match_score, moment_label, confidence, consensus, auto, category,"
            " fragment_type, evidence_features, reason, intent_score, status, created_at,"
            " batch_id, batch_rank) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?)",
            (scenes[(c["movie_title"], c["movie_year"])]["id"], "love_batch",
             c["timecode_label"], c["timecode_seconds"], c["timecode_end_label"],
             c["timecode_end_seconds"], c["source"], c["source_license"], c["evidence_sha1"],
             c["line_index"], c["match_score"], c["moment_label"], c["confidence"],
             c["consensus"], 0, c["category"], c["fragment_type"],
             json.dumps(c["evidence_features"], ensure_ascii=False), c["context"],
             c["intent_score"], now_iso(), BATCH_ID, c["batch_rank"]),
        )
    conn.commit()
    conn.close()
    print(f"Готово: {len(entries)} отрывков / {len(seen)} фильмов в очереди '{BATCH_LABEL}', "
          f"verified=0, version_note обязателен")


def _write_doc(data):
    lines = [
        f"# 20. Блок модерации №1 — {data['label']}",
        "",
        f"**{data['stats']['count']} отрывков · {data['stats']['unique_scenes']} разных фильмов · "
        f"SRT-окон: {data['stats']['srt_evidence']} · "
        f"непроверенных ориентиров: {data['stats']['starting_orienteers']} "
        f"(внешних: {data['stats'].get('external_orienteers', 0)}) · "
        f"{data['stats']['primary_love_confession']} сцен с основным интентом «признание в любви» · "
        f"проверено вручную: 0.**",
        "",
        "Это подготовленные кандидаты, а не проверенная библиотека. Для части фильмов "
        "окно 25–75 секунд построено вокруг строки локального SRT; для двух фильмов "
        "использован внешний ориентир Clip.Cafe, для остальных — уникальный непроверенный "
        "начальный ориентир по структуре фильма. Текст субтитров не сохранён. "
        "Ссылки в админке ведут только "
        "в VK Видео и Rutube. `verified=1` появится только после «✓ Подтвердить» "
        "с обязательным `version_note`.",
        "",
        "| # | Фильм | Таймкод | Что смотреть | Эмоция | Контекст/якорь | Источник |",
        "|---:|---|---|---|---|---|---|",
    ]
    for c in data["entries"]:
        title = f"{c['movie_title']} ({c['movie_year']})"
        tc = f"{c['timecode_label']}–{c['timecode_end_label']}"
        tags = ", ".join(c["emotion_tags"])
        ctx = c["context"].replace("|", "/").replace("\n", " ")[:110]
        src = ("Локальный SRT" if c["evidence_kind"] == "srt_window"
               else "Внешний ориентир" if c["evidence_kind"] == "external_orienteer"
               else "Начальный ориентир")
        lines.append(f"| {c['batch_rank']} | {title} | `{tc}` | {c['moment_label']} | {tags} | {ctx} | {src} |")
    lines += [
        "",
        "## Как модерировать",
        "",
        "1. Открой `#/admin`: фильтр «💘 Блок: признание в любви (50)» уже выбран по умолчанию. Если переключился на основную очередь — верни его обратно.",
        "2. Вверху экрана есть сводная таблица всех 50 таймкодов, а в каждой карточке — крупный бейдж с `начало–конец`. Для SRT-окон сохранены sha1 и номер строки; для ориентиров — sha1 ориентира без строки SRT. Также видны контекст, emotion tags, ссылки VK/Rutube и `mpv --start=…`.",
        "3. Открой фильм на нужной секунде, поправь минуты под своё издание, обязательно укажи `version_note`.",
        "4. Нажми «✓ Подтвердить» — только тут ставится `verified=1`. «✗ Отклонить» оставляет `verified=0`.",
        "",
        "## Оговорки",
        "",
        "- издание, реставрация 4K, ТВ-монтаж или серия могут сдвинуть таймкод;",
        "- внешние страницы сцен использованы только как контекстные подсказки, не как доказанный таймкод;",
        "- начальный ориентир не является проверенным таймкодом и не может получить `verified=1` без ручного отсмотра;",
        "- точные VK `owner_id/video_id` не выдумываются: при их отсутствии первая ссылка — поиск официального канала;",
        "- локальные `mpv`/VLC/IINA — своя легальная копия, не сторонний источник.",
        "",
        "## Внешние подсказки (не проверенные таймкоды)",
        "",
    ]
    for c in data["entries"]:
        if c.get("external_refs"):
            note = c.get("external_note", "").replace("|", "/")
            lines.append(f"- **{c['movie_title']} ({c['movie_year']})**: "
                         + ", ".join(f"<{u}>" for u in c["external_refs"])
                         + (f" — {note}" if note else ""))
    lines += [
        "",
        "Связано: [15-moderation-links.md](15-moderation-links.md), "
        "[16-moderation-board.md](16-moderation-board.md), [18-curator-workflow.md](18-curator-workflow.md).",
        "",
    ]
    with open(OUT_DOC, "w", encoding="utf-8") as f:
        f.write("\n".join(line.rstrip() for line in lines))


if __name__ == "__main__":
    main()
