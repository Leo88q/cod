#!/usr/bin/env python3
"""Собирает библиотеку таймкодов доказательным отбором и публикует доказанное.

Заменяет ручную сверку как основной механизм. Для каждой сцены:

1. собирает **издания** — независимые файлы субтитров из SRT-зеркал GitHub и/или
   локального корпуса OPUS / OpenSubtitles v2024 (`en.zip`, `ru.zip`, …);
2. отбирает доказательный фрагмент (`app/evidence_engine.py`): якорь по интенту,
   подтверждение в других изданиях выравниванием по содержанию, gate по
   плотности лексикона и негативным маркерам;
3. пишет результат:
     * `auto`   → сцена получает таймкод и публикуется в приложении
                  (`scenes.timecode_*`, `evidence_kind='auto'`),
                  `verified` НЕ ставится — это значит «не проверено человеком»;
     * `review` → кандидат в очередь `pending`, сцена остаётся без таймкода;
     * `none`   → ничего не пишем (честное «нет доказательства»).

Текст субтитров не сохраняется: только секунды, sha1 окна и агрегированные
признаки (consensus, ratio, сдвиги изданий).

Запуск:
    # издания из GitHub-зеркал (кэш скачается сам)
    .venv/bin/python scripts/build_evidence_library.py --dry-run
    .venv/bin/python scripts/build_evidence_library.py --write

    # с локальным корпусом OPUS (скачать архивы см. docs/14)
    .venv/bin/python scripts/build_evidence_library.py --opus-dir ~/Desktop/opus-xml --write
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))

from app import config  # noqa: E402
from app import evidence_engine as E  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.intent_taxonomy import category_by_id, intent_for, load_taxonomy  # noqa: E402
from import_opus_timecodes import (  # noqa: E402
    imdb_from_member, load_imdb_lookup, parse_opus_documents,
)
import fetch_timecodes as ft  # noqa: E402

LIB_DIR = os.path.join(BASE_DIR, "data", "timecode_library")
DEFAULT_MANIFEST = os.path.join(LIB_DIR, "full_manifest.json")
DEFAULT_CACHE = os.path.join(LIB_DIR, "cache")
LOCAL_MANIFEST = os.path.join(DEFAULT_CACHE, "_local_manifest.json")
IMPORT_SCRIPT = os.path.join(BASE_DIR, "scripts", "import_timecode_library.py")


def ensure_cache(manifest_path: str, cache_dir: str, refresh: bool = False) -> dict:
    """Гарантирует локальные копии SRT и возвращает локальный манифест."""
    if os.path.exists(LOCAL_MANIFEST) and not refresh:
        with open(LOCAL_MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    cmd = [sys.executable, IMPORT_SCRIPT, "--manifest", manifest_path,
           "--cache-dir", cache_dir, "--db", "/tmp/_evidence_bootstrap.db", "--dry-run"]
    print("== Кэш субтитров пуст — скачиваю издания (dry-run импорта)")
    subprocess.run(cmd, check=True, cwd=BASE_DIR)
    with open(LOCAL_MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def editions_from_github(local: dict) -> dict[tuple[str, int | None], list[E.Edition]]:
    """-> {(название, год): [Edition]} из SRT-зеркал (язык берётся из манифеста)."""
    out: dict[tuple[str, int | None], list[E.Edition]] = defaultdict(list)
    seen: set[tuple[str, int | None, str]] = set()
    for entry in local.get("entries", []):
        lang = entry.get("lang", "en")
        for scene in entry.get("scenes", []):
            title, year = scene.get("match_title"), entry.get("movie_year")
            for path in entry.get("file", []):
                if not path.endswith(".srt") or not os.path.exists(path):
                    continue
                key = (title, year, os.path.basename(path))
                if key in seen:
                    continue
                blocks = ft.parse_srt(path)
                if blocks:
                    seen.add(key)
                    out[(title, year)].append(
                        E.Edition(os.path.basename(path), lang, E.make_windows(blocks)))
    return out


def editions_from_opus(opus_dir: str, ids_path: str) -> dict[tuple[str, int | None], list[E.Edition]]:
    """-> то же, но из локальных OPUS XML-архивов (OpenSubtitles v2024).

    Имя архива `en.zip` / `ru.zip` задаёт язык издания. Внутри — `<imdb>/<imdb>.xml`,
    поэтому издания разных языков одного фильма сравниваются напрямую.
    """
    import zipfile

    lookup = load_imdb_lookup(ids_path)
    out: dict[tuple[str, int | None], list[E.Edition]] = defaultdict(list)
    if not os.path.isdir(opus_dir):
        return out
    # В одном <imdb>.xml лежат ВСЕ субтитры фильма от разных загрузчиков — это
    # разные издания, но с разными ID и общим таймлайном. Поэтому файл режется
    # на документы по <document id=...>: иначе консенсус был бы фиктивным.
    for zip_name in sorted(n for n in os.listdir(opus_dir) if n.lower().endswith(".zip")):
        lang = os.path.splitext(zip_name)[0].split(".")[0].lower()[:2] or "en"
        with zipfile.ZipFile(os.path.join(opus_dir, zip_name)) as zf:
            for member in zf.namelist():
                if not member.lower().endswith(".xml"):
                    continue
                targets = []
                for imdb in imdb_from_member(member):
                    targets.extend(lookup.get(imdb, []))
                if not targets:
                    continue
                with zf.open(member) as stream:
                    docs = parse_opus_documents(stream)
                for doc_no, blocks in enumerate(docs, 1):
                    if not blocks:
                        continue
                    wins = E.make_windows(blocks)
                    if not wins:
                        continue
                    name = f"opus:{zip_name}:{os.path.basename(member)}#{doc_no}"
                    for title, year in targets:
                        out[(title, year)].append(E.Edition(name, lang, wins))
    return out


def build(db_path: str, manifest: str, cache_dir: str, opus_dir: str | None,
          ids_path: str, min_editions: int, min_ratio: float, min_evidence: float,
          refresh: bool = False, min_intent_confidence: float = 0.7) -> list[E.Candidate]:
    local = ensure_cache(manifest, cache_dir, refresh=refresh)
    raw: dict = defaultdict(list)
    for key, eds in editions_from_github(local).items():
        raw[key].extend(eds)
    if opus_dir:
        for key, eds in editions_from_opus(opus_dir, ids_path).items():
            raw[key].extend(eds)

    tax = load_taxonomy()
    # idf считается по ВСЕМУ корпусу сразу: внутри одной сцены (2 издания) все
    # слова встречаются в обоих файлах и получают одинаковый вес, то есть
    # редкость не измеряется вообще.
    idf_by_lang: dict[str, dict[str, float]] = {}
    for lang in {ed.lang for eds in raw.values() for ed in eds}:
        pool = [(ed.name, ed.windows) for eds in raw.values() for ed in eds if ed.lang == lang]
        idf_by_lang[lang] = E.corpus_idf(pool, lang)
    conn = connect(db_path)
    results: list[E.Candidate] = []
    for (title, year), editions in raw.items():
        scene_row = conn.execute(
            "SELECT id FROM scenes WHERE movie_title=? AND movie_year=? AND status='approved'",
            (title, year)).fetchone()
        if not scene_row:
            continue
        intent = intent_for(title, year)
        cat = category_by_id(intent.get("primary_intent"))
        if not cat:
            continue
        def lexicon_for(lang: str, cat=cat) -> E.Lexicon:
            """Лексикон нужного языка: EN-издание проверяем EN-словами и наоборот."""
            if lang == "ru":
                lex = E.Lexicon.from_terms(cat["lexicon_ru"], [])
            else:
                lex = E.Lexicon.from_terms([], cat["lexicon_en"])
            return lex.with_corpus_idf(idf_by_lang.get(lang, {}))

        cand = E.select_scene(
            title, year, intent, lexicon_for("en"), editions,
            min_editions=min_editions, min_ratio=min_ratio,
            min_evidence=min_evidence if min_evidence else tax.get("min_evidence", E.MIN_EVIDENCE),
            lexicon_for=lexicon_for,
            min_intent_confidence=min_intent_confidence,
        )
        if not cand:
            continue
        cand.scene_id = scene_row["id"]  # type: ignore[attr-defined]
        results.append(cand)
    conn.close()
    # на сцену — один лучший результат (по числу подтверждений, затем по скору)
    best: dict[str, E.Candidate] = {}
    for c in results:
        cur = best.get(c.scene)
        if cur is None or (c.consensus, c.score) > (cur.consensus, cur.score):
            best[c.scene] = c
    return list(best.values())


def persist(db_path: str, cands: list[E.Candidate], min_editions: int) -> dict:
    """Записывает результаты: auto → публикация у сцены, review → очередь."""
    conn = connect(db_path)
    stats = defaultdict(int)
    for c in cands:
        scene_id = getattr(c, "scene_id", None)
        if not scene_id:
            continue
        report = json.dumps({
            "engine": "evidence_engine/1",
            "verdict": c.verdict,
            "consensus": c.consensus,
            "editions": c.editions,
            "languages": sorted({e.split(":")[1].split(".")[0] for e in c.editions if e.startswith("opus:")}) or None,
            "matches": [
                {"edition": m.edition, "ratio": m.ratio, "skew": m.skew,
                 "start": int(round(m.start)), "end": int(round(m.end))}
                for m in c.matches
            ],
            "evidence_weight": c.evidence,
            "terms_count": len(c.terms),
            "start": int(round(c.anchor.start)), "end": int(round(c.anchor.end)),
        }, ensure_ascii=False)
        label, end_label = ft.fmt_tc(c.anchor.start), ft.fmt_tc(c.anchor.end)
        status = "approved" if c.verdict == "auto" else "pending"
        conn.execute(
            "INSERT OR IGNORE INTO timecode_candidates"
            " (scene_id, service, timecode_label, timecode_seconds, timecode_end_label,"
            "  timecode_end_seconds, source, source_license, evidence_sha1, line_index,"
            "  match_score, moment_label, confidence, consensus, auto, category,"
            "  fragment_type, evidence_features, reason, intent_score, status,"
            "  created_at, reviewed_at, reviewed_by, evidence_report)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (scene_id, "evidence", label, int(round(c.anchor.start)), end_label,
             int(round(c.anchor.end)), c.editions[0], "multy-edition evidence (see docs/21)",
             c.sha1, c.anchor.line, c.score, c.reason, c.score, c.consensus,
             1 if c.verdict == "auto" else 0, c.intent, c.fragment_type,
             json.dumps({"evidence_weight": c.evidence, "hits": c.hits}, ensure_ascii=False),
             c.reason, c.score, status, _now(),
             _now() if status == "approved" else None,
             "evidence-engine" if status == "approved" else None, report),
        )
        stats[c.verdict] += 1
        if c.verdict == "auto":
            # Публикуем таймкод, но НЕ ставим verified=1: это флаг человеческой
            # проверки, и подменять его автоматикой нельзя.
            conn.execute(
                "UPDATE scenes SET timecode_label=?, timecode_seconds=?,"
                " timecode_end_label=?, timecode_end_seconds=?,"
                " evidence_kind='auto', evidence_score=?, evidence_editions=?,"
                " evidence_report=?, evidence_at=? WHERE id=?",
                (label, int(round(c.anchor.start)), end_label, int(round(c.anchor.end)),
                 c.score, c.consensus, report, _now(), scene_id),
            )
            stats["published"] += 1
    conn.commit()
    conn.close()
    return dict(stats)


def _now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=config.DB_PATH)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE)
    ap.add_argument("--opus-dir", default=None, help="каталог с en.zip/ru.zip OPUS v2024")
    ap.add_argument("--ids", default=os.path.join(LIB_DIR, "imdb_map_full.json"))
    ap.add_argument("--min-editions", type=int, default=2,
                    help="сколько независимых изданий должно сойтись (2 = пара, 3 = строже)")
    ap.add_argument("--min-ratio", type=float, default=0.55,
                    help="доля отпечатков окна, найденная в другом издании")
    ap.add_argument("--min-evidence", type=float, default=0.0,
                    help="минимальный вес лексикона в окне (0 = из таксономии)")
    ap.add_argument("--min-intent-confidence", type=float, default=0.7,
                    help="ниже этой уверенности разметки интента сцена не публикуется сама")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if not args.dry_run and not args.write:
        ap.error("нужен --dry-run или --write")

    if not args.dry_run:
        init_db(args.db)

    cands = build(args.db, args.manifest, args.cache_dir, args.opus_dir, args.ids,
                  args.min_editions, args.min_ratio, args.min_evidence, args.refresh,
                  args.min_intent_confidence)
    auto = [c for c in cands if c.verdict == "auto"]
    review = [c for c in cands if c.verdict == "review"]
    print(f"== Сцен с доказательством: auto={len(auto)}, review={len(review)}, всего={len(cands)}")
    for c in sorted(cands, key=lambda x: (x.verdict != "auto", -x.consensus))[:20]:
        print(f"   {c.verdict:6} {c.scene[:32]:32} {ft.fmt_tc(c.anchor.start)}–{ft.fmt_tc(c.anchor.end)}"
              f" cons={c.consensus} | {c.reason[:70]}")
    if args.dry_run:
        return 0
    stats = persist(args.db, cands, args.min_editions)
    print(f"== Записано: {stats}")
    conn = connect(args.db)
    row = conn.execute("SELECT COUNT(*) AS n, SUM(verified) AS v FROM scenes").fetchone()
    pub = conn.execute("SELECT COUNT(*) AS n FROM scenes WHERE evidence_kind='auto'").fetchone()["n"]
    conn.close()
    print(f"== Сцены: {row['n']}, из них с таймкодом evidence=auto: {pub}, verified (человек): {row['v'] or 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
