#!/usr/bin/env python3
"""Строит `data/timecode_library/full_manifest.json` для всего каталога.

Берёт:
  * `imdb_map_full.json` — RU-название|год -> English title + IMDb ID;
  * `source_registry.json` + `srt_index.json` — 4 внешних зеркала субтитров;
  * `pilot_manifest.json` — кураторские keyword-группы для 20 фильмов;
  * `scripts/catalog_data_a.py` / `b.py` — наши описания моментов.

НЕ скачивает субтитры и НЕ утверждает таймкоды:manifest содержит только ссылки
на SRT/CSV и наши ключевые слова. Импортёр позже извлечёт ТОЛЬКО время+sha1.
Сопоставление по названию консервативное: точное normalized-совпадение имени
файла с английским названием / его вариантом без артикля / частью до двоеточия.
Сомнительные совпадения помечаются `ambiguous=True` и `match_note`.

    .venv/bin/python scripts/build_full_manifest.py
"""
import json
import os
import re
import sys
from collections import defaultdict
from urllib.parse import quote

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(BASE_DIR, "data", "timecode_library")
sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))
from catalog_data_a import SCENES_A  # noqa: E402
from catalog_data_b import SCENES_B  # noqa: E402

IDS = os.path.join(LIB_DIR, "imdb_map_full.json")
REGISTRY = os.path.join(LIB_DIR, "source_registry.json")
INDEX = os.path.join(LIB_DIR, "srt_index.json")
PILOT = os.path.join(LIB_DIR, "pilot_manifest.json")
OUT = os.path.join(LIB_DIR, "full_manifest.json")

LICENSE = ("OpenSubtitles community subtitles via GitHub mirrors; "
           "only timecodes + sha1 stored, subtitle text is not reproduced. See docs/06-timecode-sources.md")

# Явные псевдонимы там, где имя файла в библиотеке отличается от нашего English title.
ALIASES = {
    "Властелин колец: Братство кольца|2001": ["The Lord of the Rings"],
    "Властелин колец: Две крепости|2002": ["The Lord of the Rings"],
    "Властелин колец: Возвращение короля|2003": ["The Lord of the Rings"],
    "Крёстный отец 2|1974": ["The Godfather: Part II", "The Godfather Part II"],
    "Мстители: Финал|2019": ["Avengers - Endgame", "Avengers: Endgame"],
    "Человек-паук: Нет пути домой|2021": ["Spider-Man - No Way Home"],
    "Величайшее шоу на Земле|2017": ["The Greatest Showman"],
    "ВАЛЛ-И|2008": ["WALL*E", "WALL-E"],
    "Монстры, корпорация|2001": ["Monsters, Inc."],
    "Корпорация монстров|2001": ["Monsters, Inc."],
    "Список Шиндлера|1993": ["Schindler's List"],
    "Пианист|2002": ["The Pianist"],
    "Престиж|2006": ["The Prestige"],
    "Гарри Поттер и Дары смерти|2011": ["Harry Potter and the Deathly Hallows: Part 2"],
    "Пираты Карибского моря|2003": ["Pirates of the Caribbean: The Curse of the Black Pearl"],
}

# Названия, у которых в корпусе легко найти другой фильм с таким же English title.
AMBIGUOUS_TITLES = {
    "The Island", "The Return", "Brother", "War and Peace", "Dune", "Ghost", "Invasion",
    "Attraction", "Leviathan", "The Beginning", "Office", "The Girls", "Wizards",
    "Courier", "Assa", "Garage", "Stalingrad", "The Lord of the Rings",
}

# Совпадения, которые мы сознательно НЕ пускаем в очередь: слишком легко принять
# другой фильм/другую часть за наш. Оператор найдёт их через OPUS или вручную.
EXCLUDE_KEYS = {
    "Остров|2006": "English 'The Island' в корпусе — другой фильм (не «Остров» 2006)",
    "Дюна|2021": "SRT 'Dune' в корпусе относится к экранизации 1984, а не к фильму 2021",
    "Человек-паук: Через вселенные|2023": "префикс 'Spider-Man' указывает на другой фильм",
    "Властелин колец: Братство кольца|2001": "найден только общий 'The Lord of the Rings.srt' без части/года",
    "Властелин колец: Две крепости|2002": "найден только общий 'The Lord of the Rings.srt' без части/года",
    "Властелин колец: Возвращение короля|2003": "найден только общий 'The Lord of the Rings.srt' без части/года",
}

# Для этих названий нельзя отбрасывать часть после двоеточия: префикс — другая картина.
NO_PREFIX_SPLIT = {
    "Человек-паук: Через вселенные|2023",
    "Звёздные войны: Новая надежда|1977",
    "Звёздные войны: Империя наносит ответный удар|1980",
}

GENERIC_KEYWORDS = [
    "love", "friend", "family", "home", "hope", "dream", "time", "life", "father",
    "mother", "music", "dance", "war", "city", "sea", "sky", "star", "road", "heart",
    "night", "day", "together", "always", "never", "remember", "believe", "away",
    "find", "lost", "help", "sorry", "thank", "promise", "choose", "begin", "end",
]
STOPWORDS = {"the", "a", "an", "of", "and", "in", "on", "at", "to", "for", "with",
            "part", "ii", "iii", "2", "3", "or", "is", "it", "my", "me", "you"}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def path_key(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[._-]+", " ", stem)
    stem = re.sub(r"\b(19|20)\d{2}\b", "", stem)
    return norm(stem)


def path_year(path: str) -> int | None:
    m = re.search(r"\b(19|20)(\d{2})\b", os.path.basename(path))
    return int(m.group(0)) if m else None


def variants(en: str, aliases: list[str], allow_prefix_split: bool = True) -> list[tuple[str, int]]:
    """(normalized key, бонус за точность варианта)."""
    out: list[tuple[str, int]] = []
    def add(s: str, bonus: int) -> None:
        n = norm(s)
        if len(n) >= 2 and (n, bonus) not in out:
            out.append((n, bonus))
    add(en, 3)
    for s in [en] + aliases:
        if allow_prefix_split:
            add(re.split(r"[:–—]", s)[0], 2)
        n = norm(s)
        for art in ("the", "a", "an"):
            if n.startswith(art) and len(n) > len(art) + 2:
                add(s[len(art):].lstrip(" :"), 1)
    for a in aliases:
        add(a, 2)
    return out


def title_keywords(en: str, curated: list[str] | None) -> list[str]:
    if curated:
        return curated
    words = [w for w in re.findall(r"[A-Za-z0-9]+", en or "") if w.lower() not in STOPWORDS and len(w) > 2]
    return list(dict.fromkeys(words + GENERIC_KEYWORDS))[:14]


def ru_keywords(desc: str) -> list[str]:
    words = re.findall(r"[а-яёА-ЯЁ]{4,}", desc or "")
    return list(dict.fromkeys(w.lower() for w in words))[:8]


def main() -> int:
    with open(IDS, encoding="utf-8") as f:
        id_map = json.load(f)["scenes"]
    with open(REGISTRY, encoding="utf-8") as f:
        sources = {s["id"]: s for s in json.load(f)["sources"]}
    with open(INDEX, encoding="utf-8") as f:
        index = json.load(f)["paths"]
    with open(PILOT, encoding="utf-8") as f:
        pilot = json.load(f)["entries"]
    pilot_by_title = {}
    for e in pilot:
        for sc in e.get("scenes", []):
            pilot_by_title[sc["match_title"]] = sc

    # нормализованный инвентарь: key -> [(source_id, path, year_in_filename)]
    inv: dict[str, list[tuple[str, str, int | None]]] = defaultdict(list)
    for sid, paths in index.items():
        for p in paths:
            inv[path_key(p)].append((sid, p, path_year(p)))

    catalog = {}
    seen = set()
    for tup in SCENES_A + SCENES_B:
        title, year, genre, era, age, moods, occ, desc, why = tup
        k = f"{title.strip()}|{year}"
        if k in seen or not title.strip():
            continue
        seen.add(k)
        catalog[k] = {"title": title.strip(), "year": year, "description": desc, "why_fits": why,
                      "genre": genre, "moods": moods}

    entries, unmatched, ambiguous, excluded = [], [], [], []
    used_paths: dict[str, list[str]] = defaultdict(list)
    for key, meta in catalog.items():
        im = id_map.get(key, {})
        en = im.get("original_title") or ""
        aliases = ALIASES.get(key, [])
        if key in EXCLUDE_KEYS:
            reason = "исключено: " + EXCLUDE_KEYS[key]
            excluded.append({"key": key, "english_title": en, "reason": EXCLUDE_KEYS[key]})
            unmatched.append({"key": key, "english_title": en, "reason": reason})
            continue
        if not en:
            unmatched.append({"key": key, "reason": "нет English title"})
            continue
        scored: list[tuple[int, str, str, int | None]] = []
        exact_full = norm(en)
        for vkey, bonus in variants(en, aliases, allow_prefix_split=key not in NO_PREFIX_SPLIT):
            for sid, path, pyear in inv.get(vkey, []):
                score = bonus
                if pyear and abs(pyear - meta["year"]) == 0:
                    score += 4
                elif pyear and abs(pyear - meta["year"]) <= 1:
                    score += 2
                else:
                    score += 1
                if vkey == exact_full:
                    score += 2
                scored.append((score, sid, path, pyear))
        # убираем дубли одного и того же пути, сортируем по уверенности
        uniq = {}
        for score, sid, path, pyear in scored:
            uniq[(sid, path)] = max(uniq.get((sid, path), 0), score)
        ranked = sorted(((v, k[0], k[1], path_year(k[1])) for k, v in uniq.items()), reverse=True)
        # максимум 2 файла из РАЗНЫХ источников
        chosen, used_sources = [], set()
        for score, sid, path, pyear in ranked:
            if sid in used_sources:
                continue
            chosen.append((sid, path, score, pyear))
            used_sources.add(sid)
            if len(chosen) >= 2:
                break
        if not chosen:
            unmatched.append({"key": key, "english_title": en, "reason": "нет SRT/CSV с таким именем"})
            continue

        files, srcs, formats = [], [], []
        for sid, path, score, pyear in chosen:
            s = sources[sid]
            url = f"https://github.com/{s['repo']}/blob/{s['sha']}/{quote(path, safe='/')}"
            files.append(url)
            srcs.append(url)
            formats.append(s["format"])
            used_paths[path].append(key)

        is_amb = any(en.startswith(t) or en == t for t in AMBIGUOUS_TITLES) or \
            not any(score >= 6 for _, _, score, _ in chosen)
        notes = []
        if is_amb:
            notes.append("⚠️ Сопоставление по названию приблизительное: обязательно сверь издание/год в плеере")
        if not any(pyear for _, _, _, pyear in chosen):
            notes.append("в имени файла нет года")
        if len(chosen) == 1:
            notes.append("найдена только одна версия субтитров (консенсуса не будет)")
        note = "; ".join(notes)
        if is_amb:
            ambiguous.append({"key": key, "english_title": en, "note": note})

        curated = pilot_by_title.get(meta["title"], {})
        label = (curated.get("moment_label") or meta["description"] or "момент из фильма").strip()
        if len(label) > 95:
            label = label[:92].rstrip() + "…"
        if note:
            label = f"{label} [{note}]"

        entries.append({
            "movie_title": en,
            "movie_year": meta["year"],
            "imdb_id": im.get("imdb_id"),
            "lang": "en",
            "service": "kinopoisk",
            "source_license": LICENSE,
            "file": files,
            "sources": srcs,
            "formats": formats,
            "ambiguous": is_amb,
            "match_note": note or "точное совпадение имени файла с английским названием",
            "scenes": [{
                "match_title": meta["title"],
                "keywords": title_keywords(en, curated.get("keywords")),
                "keywords_ru": ru_keywords(meta["description"]),
                "moment_label": label,
            }],
        })

    # Один и тот же общий SRT для нескольких частей серии — явно помечаем.
    for e in entries:
        shared = [p for p in e["file"] if len(used_paths.get(p.rsplit("/blob/", 1)[-1].split("/", 3)[-1], [])) > 1]
        if shared and not e["ambiguous"]:
            e["ambiguous"] = True
            e["match_note"] += "; общий файл используется для нескольких частей серии"
            e["scenes"][0]["moment_label"] += " [общий файл серии — сверь часть вручную]"

    out = {
        "comment": ("Полный best-effort манифест для 140 сцен каталога. НЕ содержит текстов субтитров. "
                    "Кандидаты остаются pending; verified=1 ставится только вручную. "
                    "Сомнительные совпадения помечены ambiguous/match_note."),
        "generated_by": "scripts/build_full_manifest.py",
        "license_note": LICENSE,
        "stats": {
            "catalog_scenes": len(catalog),
            "matched_scenes": len(entries),
            "unmatched_scenes": len(unmatched),
            "excluded_false_matches": len(excluded),
            "ambiguous_scenes": len(ambiguous),
            "subtitle_files": sum(len(e["file"]) for e in entries),
            "sources_used": sorted({s["id"] for s in sources.values() if any(s["format"] in e["formats"] for e in entries)}),
            "pilot_curated_keywords": sum(1 for e in entries if e["scenes"][0]["match_title"] in pilot_by_title),
        },
        "unmatched": unmatched,
        "excluded": excluded,
        "ambiguous": ambiguous,
        "entries": entries,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out["stats"], ensure_ascii=False, indent=2))
    print(f"Записано: {OUT}")
    print("Не найдено SRT:", len(unmatched), "из", len(catalog))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
