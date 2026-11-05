#!/usr/bin/env python3
"""Строит `data/timecode_library/scene_intents.json`: разметка ВСЕХ 146 сцен.

Две оси: один основной интент + ожидаемый тип фрагмента. Приоритет у
кураторских оверрайдов (исправляем старые расплывчатые теги вроде
«признание» у «Матрицы» и «Шрека»); остальные сцены классифицируются
детерминированно по description/why_fits/тегам/подборкам.

Запуск:  .venv/bin/python scripts/build_scene_intents.py
"""
import json
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app import config  # noqa: E402

LIB = os.path.join(BASE_DIR, "data", "timecode_library")
TAXONOMY = os.path.join(LIB, "intent_taxonomy.json")
FULL_MANIFEST = os.path.join(LIB, "full_manifest.json")
OUT = os.path.join(LIB, "scene_intents.json")

# Кураторские якоря: title|year -> (интент, тип фрагмента, конкретный эпизод)
# Эпизод — НЕ таймкод и НЕ выдуманная минута, а смысл сцены, которую ищем.
MANUAL = {
    "Служебный роман|1977": ("love_confession", "dialogue", "Робкое объяснение Новосельцева и Калугиной в чувствах, а не сцена переодевания"),
    "Когда Гарри встретил Салли|1989": ("love_confession", "monologue", "Финальное новогоднее признание Гарри: список того, что он любит в Салли"),
    "Ноттинг Хилл|1999": ("love_confession", "monologue", "Просьба «просто девушка, стоящая перед парнем»"),
    "Красотка|1990": ("love_confession", "dialogue", "Разговор о том, что важнее денег, и признание на лестнице"),
    "Титаник|1997": ("love_confession", "dialogue", "«Я лечу» на носу корабля и разговор о доверии"),
    "Дневник памяти|2004": ("anniversary_long_love", "letter_voiceover", "Пожилой муж читает дневник своей жене: любовь на всю жизнь"),
    "Привидение|1990": ("quiet_farewell", "dialogue", "Прощание Сэма и Молли через медиума"),
    "Виноваты звёзды|2014": ("quiet_farewell", "letter_voiceover", "Письмо/прощальные слова, принятие и светлая грусть"),
    "Девчата|1961": ("female_friendship", "dialogue", "Девушки в общежитии спорят о замужестве Тоси и поддерживают подругу"),
    "Москва слезам не верит|1979": ("female_friendship", "dialogue", "Разговор трёх подруг о жизни, любви и «всё наладится»"),
    "Барби|2023": ("female_friendship", "monologue", "Разговор Барби и женщины о том, что быть собой — уже достаточно"),
    "Ешь, молись, люби|2010": ("female_friendship", "dialogue", "Новый старт и разговор с подругой о себе"),
    "Брат|1997": ("male_friendship", "dialogue", "Разговор Данилы со старшим братом и «сила не в этом»"),
    "Брат 2|2000": ("male_friendship", "dialogue", "Данила выручает брата; братская верность"),
    "1+1|2011": ("male_friendship", "dialogue", "Разговор Филиппа и Дрисса о доверии и дружбе"),
    "Зелёная книга|2018": ("male_friendship", "dialogue", "Дон и Тони в дороге: разговор, который сближает"),
    "В бой идут одни «старики»|1973": ("memory_war", "song_music", "Лётчики поют между вылетами: песня о войне и дружбе"),
    "Семнадцать мгновений весны|1973": ("memory_war", "dialogue", "Тихий разговор Штирлица / письмо домой, без баталий"),
    "Saving Private Ryan|1998": ("memory_war", "dialogue", "Разговор о долге и письме матери, а не высадка"),
    "Список Шиндлера|1993": ("memory_war", "monologue", "Финальная речь «кто спас одну жизнь»"),
    "Побег из Шоушенка|1994": ("support_hope", "dialogue", "Энди и Рэд о надежде: «надежда — хорошая штука»"),
    "Форрест Гамп|1994": ("support_hope", "monologue", "Монолог на скамейке про коробку конфет и доброту"),
    "Вверх|2009": ("quiet_farewell", "silent_gesture", "Безсловный монтаж жизни Карла и Элли; SRT не найдёт — кураторский якорь"),
    "В поисках Немо|2003": ("family_parents", "dialogue", "Марлин ищет сына; разговор отца и ребёнка"),
    "Король Лев|1994": ("family_parents", "dialogue", "Муфаса и Симба о звёздах и о том, что он всегда рядом"),
    "Тайна Коко|2017": ("family_parents", "song_music", "«Помни меня» и разговор с прабабушкой"),
    "Гадкий я|2010": ("family_parents", "comic_gag", "Грю усыновляет девочек: семья не по плану"),
    "Любовь и голуби|1984": ("family_parents", "comic_gag", "Семейный разговор у голубятни, тёплый и смешной"),
    "Начало|1970": ("family_parents", "monologue", "Паша Строгова о материнской любви и мечте"),
    "Крёстный отец|1972": ("family_parents", "dialogue", "Вито и Майкл о семье и выборе, а не «признание»"),
    "Возвращение|2003": ("family_parents", "dialogue", "Отец и сыновья: первая встреча после возвращения"),
    "Матрица|1999": ("inspiration_dream", "dialogue", "Морфеус предлагает красную таблетку и разговор о выборе правды"),
    "Ла-Ла Ленд|2016": ("inspiration_dream", "song_music", "Разговор о мечте и кафе / финальная «what if»"),
    "Рататуй|2007": ("inspiration_dream", "letter_voiceover", "Речь критика Антуана Эго о таланте"),
    "Легенда №17|2013": ("inspiration_dream", "dialogue", "Тренер и команда перед решающим матчем"),
    "Движение вверх|2017": ("inspiration_dream", "dialogue", "Разговор о победе и вере в команду"),
    "Душа|2020": ("inspiration_dream", "dialogue", "Джо и 22 об искре и смысле жизни"),
    "Ирония судьбы, или С лёгким паром!|1975": ("holiday_comedy", "comic_gag", "Новогодняя путаница: ключ, чужая квартира, «не туда попал»"),
    "Карнавальная ночь|1956": ("holiday_comedy", "song_music", "Новогодний карнавал и песня о пяти минутах"),
    "Один дома|1990": ("holiday_comedy", "comic_gag", "Утро в доме без родителей / ловушки для грабителей"),
    "Ёлки|2010": ("holiday_comedy", "dialogue", "Новогодняя ночь, переплетение историй и поздравление"),
    "Шрек|2001": ("children_animation", "comic_gag", "Первая встреча Шрека и Осла: комедия, а не признание в любви"),
    "ВАЛЛ-И|2008": ("children_animation", "silent_gesture", "Робот ухаживает за Евой без слов; кураторский якорь"),
    "Холодное сердце|2013": ("children_animation", "song_music", "«Отпусти и забудь» / разговор сестёр"),
    "Как приручить дракона|2010": ("children_animation", "dialogue", "Иккинг и Беззубик: первый контакт и доверие"),
    "Грязные танцы|1987": ("children_animation", "song_music", "Финальный танец «I've Had the Time of My Life»"),
    "Гараж|1979": ("nostalgia_classic", "dialogue", "Собрание кооператива: узнаваемые реплики про пайщиков"),
    "Покровские ворота|1982": ("nostalgia_classic", "dialogue", "Коммунальная квартира и разговор Хоботова с Маргаритой Павловной"),
    "Бриллиантовая рука|1968": ("just_laugh", "comic_gag", "«У нас, у советских, своя гордость» / падение с бананом"),
    "Иван Васильевич меняет профессию|1973": ("just_laugh", "comic_gag", "«Иван Грозный» в квартире и путаница с машиной времени"),
    "Кавказская пленница|1966": ("just_laugh", "comic_gag", "Орёл, «птичку жалко» и охота на невесту"),
    "Операция «Ы» и другие приключения Шурика|1965": ("just_laugh", "comic_gag", "Студент на стройке / «чтобы машина пошла, надо...»"),
    "Афоня|1975": ("apology_reconciliation", "dialogue", "Афоня просит прощения и возвращается к своим"),
    "Гусарская баллада|1962": ("holiday_comedy", "comic_gag", "Маскарад с переодеванием и новогодний бал"),
    "Война и мир|1966": ("anniversary_long_love", "dialogue", "Большой семейный разговор о мире, любви и годах"),
    "Сибирский цирюльник|1998": ("love_confession", "dialogue", "Объяснение кадета и Джейн перед разлукой"),
    "Жестокий романс|2008": ("quiet_farewell", "monologue", "Тихая сцена прощания и принятия, без пафоса"),
    "Амели|2001": ("female_friendship", "monologue", "Амели решает помочь другим; разговор о добрых делах"),
    "Джентльмены|2019": ("just_laugh", "dialogue", "Стильный диалог-перепалка про империю и подставу"),
    "Джокер|2019": ("support_hope", "monologue", "Разговор о том, как тебя видят, и принятие себя"),
}

TAG_HINTS = {
    "признание": {"love_confession": 0.35},
    "поддержка": {"support_hope": 0.55},
    "извинение": {"apology_reconciliation": 0.85},
    "друг": {"male_friendship": 0.30, "female_friendship": 0.20},
    "8 марта": {"female_friendship": 0.45},
    "14 февраля": {"love_confession": 0.35, "anniversary_long_love": 0.15},
    "день святого валентина": {"love_confession": 0.35},
    "годовщина": {"anniversary_long_love": 0.65},
    "новый год": {"holiday_comedy": 0.85},
    "день рождения": {"holiday_comedy": 0.35},
    "9 мая": {"memory_war": 0.55},
    "23 февраля": {"memory_war": 0.35},
    "отец": {"family_parents": 0.55},
    "мама": {"family_parents": 0.65},
    "семья": {"family_parents": 0.65},
    "рождение ребёнка": {"family_parents": 0.55},
    "для детей": {"children_animation": 0.85},
    "для старших": {"nostalgia_classic": 0.50},
    "мечта": {"inspiration_dream": 0.70},
    "свадьба": {"anniversary_long_love": 0.45},
    "любовь": {"love_confession": 0.30},
    "просто так": {"just_laugh": 0.10},
}

LIST_HINTS = {
    "💪 Поддержка": {"support_hope": 0.55},
    "🙏 Извинения": {"apology_reconciliation": 0.65},
    "🎄 Новый год": {"holiday_comedy": 0.65},
    "🎂 День рождения": {"holiday_comedy": 0.30},
    "🌷 8 марта": {"female_friendship": 0.45},
    "💘 14 февраля": {"love_confession": 0.40},
    "👶 Для детей": {"children_animation": 0.65},
    "👴 Для старших": {"nostalgia_classic": 0.45},
    "👨‍👩‍👧 Семья": {"family_parents": 0.55},
    "🎖 23 февраля / 9 мая": {"memory_war": 0.55},
}

FRAGMENT_BY_CATEGORY = {
    "love_confession": "monologue", "female_friendship": "dialogue",
    "male_friendship": "dialogue", "support_hope": "monologue",
    "apology_reconciliation": "dialogue", "family_parents": "dialogue",
    "inspiration_dream": "monologue", "memory_war": "dialogue",
    "anniversary_long_love": "toast", "holiday_comedy": "comic_gag",
    "children_animation": "dialogue", "nostalgia_classic": "dialogue",
    "just_laugh": "comic_gag", "quiet_farewell": "monologue",
}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def manifest_index():
    d = load_json(FULL_MANIFEST)
    out = {}
    for e in d.get("entries", []):
        for sc in e.get("scenes", []):
            key = f"{sc.get('match_title')}|{e.get('movie_year')}"
            out[key] = {
                "english_title": e.get("movie_title", ""),
                "keywords": sc.get("keywords", []),
                "keywords_ru": sc.get("keywords_ru", []),
            }
    return out


def score_categories(text: str, moods, occ, lists, cats):
    scores = {c["id"]: 0.0 for c in cats}
    low = text.lower()
    for c in cats:
        cid = c["id"]
        for term in c["lexicon_ru"]:
            if term in low:
                scores[cid] += 1.0
        for term in c["query_ru"].split():
            if len(term) > 4 and term in low:
                scores[cid] += 0.35
        if c["label"].lower() in low:
            scores[cid] += 0.5
    for tag in occ:
        for cid, w in TAG_HINTS.get(tag, {}).items():
            scores[cid] += w
    for mood in moods:
        if mood == "музыка":
            scores["memory_war"] += 0.15
            scores["inspiration_dream"] += 0.15
            scores["holiday_comedy"] += 0.10
        if mood == "грусть":
            scores["quiet_farewell"] += 0.30
        if mood == "ностальгия":
            scores["nostalgia_classic"] += 0.30
        if mood == "юмор":
            scores["just_laugh"] += 0.15
    for l in lists:
        for cid, w in LIST_HINTS.get(l, {}).items():
            scores[cid] += w
    return scores


def main():
    tax = load_json(TAXONOMY)
    cats = tax["categories"]
    by_id = {c["id"]: c for c in cats}
    man = manifest_index()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    lists = {}
    for r in conn.execute(
        "SELECT l.name AS lname, s.movie_title, s.movie_year FROM scene_lists l"
        " JOIN list_scenes ls ON ls.list_id=l.id JOIN scenes s ON s.id=ls.scene_id"
    ):
        lists.setdefault((r["movie_title"], r["movie_year"]), []).append(r["lname"])
    rows = conn.execute(
        "SELECT id, movie_title, movie_year, genre, era, mood_tags, occasion_tags, description, why_fits"
        " FROM scenes WHERE status='approved' ORDER BY movie_title, movie_year"
    ).fetchall()
    conn.close()

    intents = {}
    dist = {}
    for r in rows:
        key = f"{r['movie_title']}|{r['movie_year']}"
        moods = json.loads(r["mood_tags"] or "[]")
        occ = json.loads(r["occasion_tags"] or "[]")
        ls = lists.get((r["movie_title"], r["movie_year"]), [])
        text = " ".join(str(x or "") for x in (r["description"], r["why_fits"], r["movie_title"]))
        scores = score_categories(text, moods, occ, ls, cats)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], [c["id"] for c in cats].index(kv[0])))
        top_cid, top_score = ranked[0]
        if key in MANUAL:
            cid, frag, anchor = MANUAL[key]
            origin, confidence = "manual", 0.95
            secondary = [x for x, _ in ranked[1:] if _ > 0.3 * max(top_score, 0.1)][:2]
        else:
            cid = top_cid
            if top_score <= 0:
                cid = "just_laugh" if r["genre"] in ("комедия", "мультфильм") else "support_hope"
            frag = FRAGMENT_BY_CATEGORY.get(cid, "dialogue")
            if cid == "holiday_comedy" and "музыка" in moods:
                frag = "song_music"
            if cid == "children_animation" and "музыка" in moods:
                frag = "song_music"
            if cid == "memory_war" and "музыка" in moods:
                frag = "song_music"
            anchor = f"Эпизод: {r['description'] or r['movie_title']}. Искать: {by_id[cid]['query_ru']}"
            origin, confidence = "rule", round(min(0.85, 0.45 + top_score * 0.12), 2)
            secondary = [x for x, s in ranked[1:] if s > 0.35 * max(top_score, 0.1)][:2]
        cat = by_id[cid]
        m = man.get(key, {})
        query_ru = " ".join([cat["query_ru"], r["description"] or "", r["why_fits"] or "", anchor])
        query_en = " ".join([cat["query_en"], m.get("english_title", ""), " ".join(m.get("keywords", [])[:12])])
        intents[key] = {
            "primary_intent": cid,
            "intent_label": cat["label"],
            "fragment_type": frag,
            "fragment_type_label": tax["fragment_types"].get(frag, ""),
            "secondary_intents": secondary,
            "intent_query_ru": query_ru.strip(),
            "intent_query_en": query_en.strip(),
            "curated_anchor": anchor,
            "origin": origin,
            "confidence": confidence,
        }
        dist[cid] = dist.get(cid, 0) + 1

    out = {
        "version": "1.0",
        "generated_by": "scripts/build_scene_intents.py",
        "comment": "Один основной интент + тип фрагмента на каждую approved-сцену. Таймкод не выдумывается: ищется SRT-окно под эту пару или остаётся needs_curator.",
        "stats": {"scenes": len(intents), "manual": sum(1 for v in intents.values() if v["origin"] == "manual"),
                  "by_category": dist},
        "intents": intents,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("OK", len(intents), "сцен →", OUT)
    print("категории", sorted(dist.items(), key=lambda kv: -kv[1]))
    print("кураторских якорей", out["stats"]["manual"])


if __name__ == "__main__":
    main()
