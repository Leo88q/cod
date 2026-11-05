#!/usr/bin/env python3
"""Адаптер OPUS / OpenSubtitles v2024 для локальной машины оператора.

В песочнице `object.pouta.csc.fi` недоступен, поэтому корпус скачивается на Mac
через OpusTools/браузер, а этот скрипт читает уже локальные XML-архивы:

    opus-tools ...   # или вручную: https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2024/xml/en.zip
    .venv/bin/python scripts/import_opus_timecodes.py --opus-dir ~/Desktop/opus-xml

Из XML извлекаются ТОЛЬКО факты времени: start/end, sha1 нормализованной строки,
IMDb ID, source/license, confidence/consensus. Текст реплик не сохраняется.
Все кандидаты остаются `status='pending'`: `verified=1` ставится только вручную
в `#/admin` после просмотра момента в легальном плеере.

Поддерживаются два вида разметки <s>:
  * атрибуты time_start / time_end;
  * вложенные <time value="HH:MM:SS,mmm"/> + слова <w> (как в OPUS v2024).
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))
from app import config  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from fetch_timecodes import (  # noqa: E402
    cluster_consensus, fmt_tc, keyword_moments, norm,
)

LIB_DIR = os.path.join(BASE_DIR, "data", "timecode_library")
DEFAULT_IDS = os.path.join(LIB_DIR, "imdb_map_full.json")
DEFAULT_MANIFEST = os.path.join(LIB_DIR, "full_manifest.json")
DEFAULT_PILOT = os.path.join(LIB_DIR, "pilot_manifest.json")

OPUS_LICENSE = ("OPUS / OpenSubtitles v2024, ODC-BY 1.0 + attribution to opensubtitles.org; "
                "underlying subtitle rights belong to rightsholders. Only timecodes + sha1 stored.")

GENERIC_KEYWORDS = [
    "love", "friend", "family", "home", "hope", "dream", "time", "life", "father",
    "mother", "music", "dance", "war", "city", "sea", "sky", "star", "road", "heart",
    "night", "day", "together", "always", "never", "remember", "believe", "away",
    "find", "lost", "help", "sorry", "thank", "promise", "choose", "begin", "end",
]


def tag(elem) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def time_to_seconds(value: str | None) -> float | None:
    """'00:01:02,500' / '00:01:02.500' / '62.5' / '62500' -> секунды."""
    if not value:
        return None
    nums = re.findall(r"\d+", str(value))
    if not nums:
        return None
    if len(nums) >= 4:
        h, m, s, ms = (int(x) for x in nums[:4])
        return h * 3600 + m * 60 + s + ms / 1000.0
    if len(nums) == 3:
        m, s, ms = (int(x) for x in nums)
        return m * 60 + s + ms / 1000.0
    if len(nums) == 2:
        s, ms = (int(x) for x in nums)
        return s + ms / 1000.0
    n = float(nums[0])
    # OPUS value почти всегда строка времени; одиночное число трактуем как секунды,
    # а значения > 10000 — как миллисекунды.
    return n / 1000.0 if n > 10000 else n


def norm_imdb(raw: str) -> list[str]:
    """Возвращает возможные варианты tt-идентификатора из пути/имени файла."""
    raw = str(raw or "")
    m = re.search(r"(?:tt)?(\d{5,9})", raw, re.IGNORECASE)
    if not m:
        return []
    digits = m.group(1)
    out = {f"tt{digits}"}
    out.add(f"tt{digits.zfill(7)}")
    out.add(f"tt{digits.zfill(8)}")
    # OPUS часто хранит ID без ведущего нуля: 109830 -> tt0109830.
    out.add(f"tt{digits.lstrip('0').zfill(7)}")
    return [x for x in out if re.fullmatch(r"tt\d{7,8}", x)]


def imdb_from_member(name: str) -> list[str]:
    candidates = norm_imdb(name)
    parent = name.rsplit("/", 2)[-2] if "/" in name else ""
    if parent:
        stem = parent.split("_")[0]
        candidates = norm_imdb(stem) + candidates
    # убираем дубли, сохраняя порядок
    return list(dict.fromkeys(candidates))


def parse_opus_xml(stream) -> list[tuple[int, float, float, str]]:
    """-> [(line_index, start_sec, end_sec, text)] без сохранения исходного текста."""
    blocks: list[tuple[int, float, float, str]] = []
    idx = 0
    pending_start: float | None = None
    try:
        for _event, elem in ET.iterparse(stream, events=("end",)):
            t = tag(elem)
            if t == "time" and elem.get("value"):
                seconds = time_to_seconds(elem.get("value"))
                if seconds is not None and pending_start is None:
                    pending_start = seconds
            if t != "s":
                continue
            start = time_to_seconds(elem.get("time_start"))
            end = time_to_seconds(elem.get("time_end"))
            times = [time_to_seconds(e.get("value")) for e in elem.iter() if tag(e) == "time" and e.get("value")]
            times = [x for x in times if x is not None]
            if start is None:
                start = times[0] if times else pending_start
            if end is None:
                end = times[-1] if len(times) > 1 else (start + 3.0 if start is not None else None)
            if start is None or end is None or end <= start:
                end = (start or 0) + 3.0
            words = [(e.text or "").strip() for e in elem.iter() if tag(e) == "w" and (e.text or "").strip()]
            if not words:
                words = [(elem.text or "").strip()] if (elem.text or "").strip() else []
            text = " ".join(words)
            if text:
                idx += 1
                blocks.append((idx, float(start), float(end), text))
            pending_start = None
            elem.clear()
    except ET.ParseError:
        return blocks
    return blocks


def load_keywords(manifest_path: str, pilot_path: str) -> dict[tuple[str, int], dict]:
    out: dict[tuple[str, int], dict] = {}
    for path in (pilot_path, manifest_path):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for e in data.get("entries", []):
            for sc in e.get("scenes", []):
                out[(sc["match_title"], e.get("movie_year"))] = {
                    "keywords": sc.get("keywords") or GENERIC_KEYWORDS,
                    "moment_label": sc.get("moment_label") or "момент из фильма (OPUS)",
                    "service": e.get("service") or "kinopoisk",
                }
    return out


def load_imdb_lookup(ids_path: str) -> dict[str, list[tuple[str, int]]]:
    """tt-вариант -> [(movie_title, movie_year), ...]."""
    with open(ids_path, encoding="utf-8") as f:
        data = json.load(f)
    lookup: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for key, meta in data.get("scenes", data).items():
        imdb = meta.get("imdb_id")
        if not imdb:
            continue
        if "|" in key:
            title, raw_year = key.rsplit("|", 1)
            try:
                year = int(raw_year)
            except ValueError:
                year = meta.get("movie_year")
        else:
            title, year = key, meta.get("movie_year")
        if not year:
            continue
        for variant in norm_imdb(imdb):
            lookup[variant].append((title, year))
    return lookup


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--opus-dir", required=True, help="папка с локальными *.zip OPUS XML")
    ap.add_argument("--db", default=config.DB_PATH)
    ap.add_argument("--ids", default=DEFAULT_IDS)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--pilot", default=DEFAULT_PILOT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit-files", type=int, default=0, help="обработать только N XML (для тестов)")
    ap.add_argument("--max-per-scene", type=int, default=3)
    ap.add_argument("--min-consensus", type=int, default=2)
    ap.add_argument("--auto-threshold", type=float, default=0.45)
    args = ap.parse_args()

    keywords = load_keywords(args.manifest, args.pilot)
    imdb_lookup = load_imdb_lookup(args.ids)
    conn = None if args.dry_run else connect(args.db)
    scene_cache: dict[tuple[str, int], list[int]] = {}

    def scene_ids(title: str, year: int) -> list[int]:
        if args.dry_run:
            return [-1]
        k = (title, year)
        if k not in scene_cache:
            rows = conn.execute("SELECT id FROM scenes WHERE movie_title=? AND movie_year=?", k).fetchall()
            scene_cache[k] = [r[0] if not isinstance(r, dict) else r["id"] for r in rows]
        return scene_cache[k]

    per_scene: dict[int, list[dict]] = defaultdict(list)
    zips = sorted(p for p in os.listdir(args.opus_dir) if p.lower().endswith(".zip")) \
        if os.path.isdir(args.opus_dir) else []
    if not zips:
        print(f"В {args.opus_dir} нет *.zip — сначала скачай OPUS XML на локальную машину.")
        return 1
    processed = 0
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for zip_name in zips:
        zip_path = os.path.join(args.opus_dir, zip_name)
        with zipfile.ZipFile(zip_path) as zf:
            members = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            for member in members:
                matched = False
                ids = imdb_from_member(member)
                targets: list[tuple[str, int, list[int]]] = []
                for imdb in ids:
                    for title, year in imdb_lookup.get(imdb, []):
                        targets.append((title, year, scene_ids(title, year)))
                if not targets:
                    continue
                with zf.open(member) as stream:
                    blocks = parse_opus_xml(stream)
                if not blocks:
                    continue
                source = f"opus://{zip_name}/{member}"
                for title, year, sids in targets:
                    kw = keywords.get((title, year), {})
                    cands = keyword_moments(blocks, kw.get("keywords") or GENERIC_KEYWORDS, lang="en", top_n=args.max_per_scene)
                    for c in cands:
                        entry = {
                            "file": source,
                            "source": source,
                            "cand": c,
                            "moment_label": kw.get("moment_label") or "момент из фильма (OPUS)",
                            "service": kw.get("service") or "kinopoisk",
                            "title": title,
                        }
                        for sid in sids or ([-1] if args.dry_run else []):
                            per_scene[sid].append(dict(entry))
                processed += 1
                matched = True
                if args.limit_files and processed >= args.limit_files:
                    break
            if args.limit_files and processed >= args.limit_files:
                break
        if args.limit_files and processed >= args.limit_files:
            break
    if not processed:
        print("Не найдено XML с IMDb ID, совпадающими с нашим каталогом.")
        return 0

    total = 0
    for sid, entries in per_scene.items():
        merged = cluster_consensus(entries)
        merged.sort(key=lambda x: (x["confidence"], x["score"]), reverse=True)
        if args.max_per_scene > 0:
            merged = merged[:args.max_per_scene]
        for m in merged:
            c = m["cand"]
            auto = 1 if (m["consensus"] >= args.min_consensus and m["confidence"] >= args.auto_threshold) else 0
            sha = hashlib.sha1(norm(c["text"]).encode("utf-8")).hexdigest()
            label = fmt_tc(m["start"])
            end_label = fmt_tc(m["end"])
            if args.dry_run:
                print(f"   [dry] scene={sid} ⏱ {label}–{end_label} "
                      f"confidence={m['confidence']:.3f} consensus={m['consensus']} auto={auto}")
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO timecode_candidates"
                    " (scene_id, service, timecode_label, timecode_seconds, timecode_end_label,"
                    "  timecode_end_seconds, source, source_license, evidence_sha1, line_index,"
                    "  match_score, moment_label, confidence, consensus, auto, status, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)",
                    (sid, entries[0]["service"], label, int(round(m["start"])),
                     end_label, int(round(m["end"])), m["source"], OPUS_LICENSE, sha, c["line"],
                     round(m["score"], 4), entries[0]["moment_label"], m["confidence"],
                     m["consensus"], auto, now),
                )
            total += 1
    if not args.dry_run:
        conn.commit()
        pending = conn.execute("SELECT COUNT(*) FROM timecode_candidates WHERE status='pending'").fetchone()[0]
        conn.close()
        print(f"== OPUS: обработано XML: {processed}, новых кандидатов: {total}, всего pending: {pending}")
    else:
        print(f"== OPUS dry-run: обработано XML: {processed}, кандидатов: {total}")
    print("== Дальше: #/admin → ручная сверка в легальном плеере. Авто-одобрение НЕ выполнялось.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
