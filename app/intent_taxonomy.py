"""Загрузка таксономии интентов и разметки 146 сцен.

Источник истины — `data/timecode_library/intent_taxonomy.json` и
`scene_intents.json`. В БД поля дублируются только для удобства SQL/админки;
текст субтитров здесь не хранится вообще.
"""
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(BASE_DIR, "data", "timecode_library")
TAXONOMY_PATH = os.path.join(LIB, "intent_taxonomy.json")
INTENTS_PATH = os.path.join(LIB, "scene_intents.json")

_TAX = None
_INTENTS = None


def load_taxonomy() -> dict:
    global _TAX
    if _TAX is None:
        with open(TAXONOMY_PATH, encoding="utf-8") as f:
            _TAX = json.load(f)
    return _TAX


def load_scene_intents() -> dict:
    global _INTENTS
    if _INTENTS is None:
        with open(INTENTS_PATH, encoding="utf-8") as f:
            _INTENTS = json.load(f)
    return _INTENTS


def categories() -> list[dict]:
    return load_taxonomy()["categories"]


def category_by_id(cid: str) -> dict | None:
    return next((c for c in categories() if c["id"] == cid), None)


def fragment_types() -> dict:
    return load_taxonomy()["fragment_types"]


def intent_key(title: str, year: int | None) -> str:
    return f"{title}|{year or ''}"


def intent_for(title: str, year: int | None) -> dict:
    """Возвращает разметку сцены; для непредвиденной сцены — безопасный дефолт."""
    data = load_scene_intents()["intents"]
    found = data.get(intent_key(title, year))
    if found:
        return found
    cat = category_by_id("support_hope")
    return {
        "primary_intent": "support_hope", "intent_label": cat["label"],
        "fragment_type": "dialogue", "fragment_type_label": fragment_types()["dialogue"],
        "secondary_intents": [], "intent_query_ru": cat["query_ru"],
        "intent_query_en": cat["query_en"], "curated_anchor": f"Эпизод: {title}",
        "origin": "fallback", "confidence": 0.2,
    }


def backfill_intents(conn) -> int:
    """Проставляет primary_intent/fragment_type/anchor всем approved-сценам."""
    data = load_scene_intents()["intents"]
    rows = conn.execute(
        "SELECT id, movie_title, movie_year FROM scenes WHERE status='approved'"
    ).fetchall()
    n = 0
    for r in rows:
        v = data.get(intent_key(r["movie_title"], r["movie_year"]))
        if not v:
            continue
        conn.execute(
            "UPDATE scenes SET primary_intent=?, fragment_type=?, intent_query=?,"
            " curated_anchor=?, intent_confidence=?, intent_source=? WHERE id=?",
            (v["primary_intent"], v["fragment_type"], v["intent_query_ru"],
             v["curated_anchor"], v["confidence"], v["origin"], r["id"]),
        )
        n += 1
    conn.commit()
    return n
