# -*- coding: utf-8 -*-
"""Генерация db/seed_catalog.sql из scripts/catalog_data_a.py + catalog_data_b.py.

Запуск:  .venv/bin/python scripts/gen_catalog.py
Выход:  db/seed_catalog.sql  (INSERT OR IGNORE, идемпотентно; дедуп по (title, year))
Также генерирует подборки (scene_lists) по названиям фильмов.
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from catalog_data_a import SCENES_A  # noqa: E402
from catalog_data_b import SCENES_B  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db", "seed_catalog.sql")

# Подборки: название -> список названий фильмов (сверяются по title в SQL)
LISTS = {
    "💘 14 февраля": ["Когда Гарри встретил Салли", "Ноттинг Хилл", "Амели", "Ла-Ла Ленд", "Титаник", "Дневник памяти", "Привидение", "Грязные танцы", "Звезда родилась", "ВАЛЛ-И", "Чародеи", "Формула любви", "Кавказская пленница"],
    "🌷 8 марта": ["Девчата", "Любовь и голуби", "Служебный роман", "Гусарская баллада", "Карнавальная ночь", "Миссис Даутфайр", "Амели", "Барби", "Холодное сердце", "Сибирский цирюльник", "Жестокий романс", "Ешь, молись, люби"],
    "🙏 Извинения": ["Операция «Ы» и другие приключения Шурика", "Иван Васильевич меняет профессию", "Афоня"],
    "💪 Поддержка": ["Москва слезам не верит", "Побег из Шоушенка", "Вверх", "Виноваты звёзды", "1+1", "Зелёная книга", "Король Лев", "Движение вверх", "Легенда №17", "Возвращение", "Остров", "Душа"],
    "🎂 День рождения": ["Назад в будущее", "Шрек", "Король Лев", "Как приручить дракона", "Гадкий я", "В поисках Немо", "История игрушек", "Рататуй", "Один дома", "Барби", "Выкрутасы", "Холоп", "Иван Васильевич меняет профессию"],
    "🎄 Новый год": ["Ирония судьбы, или С лёгким паром!", "Один дома", "Ёлки", "Чародеи", "Карнавальная ночь", "Зимний вечер в Гаграх", "Ирония судьбы. Продолжение", "Сияние"],
    "👴 Для старших": ["В бой идут одни «старики»", "Семнадцать мгновений весны", "Белое солнце пустыни", "Утомлённые солнцем", "Девчата", "Гараж", "Старики-разбойники", "Любовь и голуби", "Вверх", "Тайна Коко", "Покровские ворота", "Зимний вечер в Гаграх", "Высоцкий. Спасибо, что живой"],
    "👶 Для детей": ["Вверх", "Король Лев", "Холодное сердце", "Как приручить дракона", "Гадкий я", "Тайна Коко", "Унесённые призраками", "Шрек", "В поисках Немо", "История игрушек", "Суперсемейка", "Рататуй", "Корпорация монстров", "Один дома", "Человек-паук: Через вселенные"],
    "👨‍👩‍👧 Семья": ["Любовь и голуби", "Миссис Даутфайр", "В поисках Немо", "Суперсемейка", "Тайна Коко", "Крёстный отец", "Война и мир", "Семнадцать мгновений весны"],
    "🎖 23 февраля / 9 мая": ["В бой идут одни «старики»", "Семнадцать мгновений весны", "Место встречи изменить нельзя", "Война и мир", "Афоня", "Т-34", "Легенда №17", "Движение вверх", "Сталинград", "Август. Восьмого", "Утомлённые солнцем", "Saving Private Ryan", "Список Шиндлера", "Пианист", "Гусарская баллада", "Брат", "Брат 2"],
}


def q(s: str) -> str:
    return "'" + (s or "").replace("'", "''") + "'"


def main() -> int:
    seen = set()
    rows = []
    for tup in SCENES_A + SCENES_B:
        title, year, genre, era, age, moods, occ, desc, why = tup
        key = (title.strip().lower(), year)
        if key in seen or not title.strip():
            continue
        seen.add(key)
        rows.append(
            "INSERT OR IGNORE INTO scenes (movie_title, movie_year, service, timecode_label, genre, era,"
            " age_rating, mood_tags, occasion_tags, description, why_fits, source, source_license, status,"
            " verified) VALUES ("
            + ",".join([
                q(title.strip()), str(year), q("kinopoisk"), "NULL", q(genre), q(era), q(age),
                q(json.dumps([t.strip() for t in moods.split(";") if t.strip()], ensure_ascii=False)),
                q(json.dumps([t.strip() for t in occ.split(";") if t.strip()], ensure_ascii=False)),
                q(desc), q(why), q("catalog"), q("manual-curation"), q("approved"), "0",
            ]) + ");"
        )
    lists_sql = ["INSERT OR IGNORE INTO users (id, first_name) VALUES (1, 'system');"]
    for name, titles in LISTS.items():
        lists_sql.append(f"INSERT OR IGNORE INTO scene_lists (user_id, name) SELECT 1, {q(name)} WHERE NOT EXISTS (SELECT 1 FROM scene_lists WHERE name={q(name)});")
        tl = ",".join(q(t) for t in titles)
        lists_sql.append(
            f"INSERT OR IGNORE INTO list_scenes (list_id, scene_id) SELECT l.id, s.id FROM scene_lists l, scenes s"
            f" WHERE l.name={q(name)} AND s.movie_title IN ({tl});")
    header = ("-- АВТОГЕНЕРАЦИЯ: scripts/gen_catalog.py — не править руками. "
              "Таймкоды NULL (verified=0) — проставляет сверка через админку.\n")
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("\n".join(rows) + "\n\n-- подборки --\n" + "\n".join(lists_sql) + "\n")
    print(f"OK: {len(rows)} сцен -> {os.path.normpath(OUT)}")
    return len(rows)


if __name__ == "__main__":
    main()
