"""Семантический поиск по каталогу сцен — локально, без внешних API.

TF-IDF по русским текстам (описание + теги + название) + косинусная близость.
Индекс кэшируется в памяти и пересобирается при изменении каталога (по кол-ву approved сцен).
"""
import threading

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

RU_STOPWORDS = [
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то", "все", "она", "так",
    "его", "но", "да", "ты", "к", "у", "же", "вы", "за", "бы", "по", "только", "ее", "мне", "было",
    "вот", "от", "меня", "еще", "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну", "ли",
    "если", "уже", "или", "ни", "быть", "был", "него", "до", "вас", "нибудь", "опять", "уж", "вам",
    "ведь", "там", "потом", "себя", "ничего", "ей", "может", "они", "тут", "где", "есть", "надо",
    "ней", "для", "мы", "тебя", "их", "чем", "была", "сам", "под", "будет", "ж", "тогда", "кто",
    "этот", "того", "потому", "этого", "какой", "совсем", "ним", "здесь", "этом", "один", "почти",
    "мой", "чтобы", "нее", "сейчас", "когда", "куда", "зачем", "всех", "никогда", "можно", "при",
    "наконец", "два", "об", "хоть", "после", "над", "больше", "тот", "через", "эти", "нас", "про",
    "всего", "них", "какая", "много", "разве", "три", "эту", "моя", "впрочем", "хорошо", "свою",
    "этой", "перед", "иногда", "лучше", "чуть", "том", "нельзя", "такой", "им", "более", "всегда",
]

_lock = threading.Lock()
_cache = {"n": -1, "ids": [], "mat": None}


def _corpus(conn) -> tuple[list[int], list[str]]:
    rows = conn.execute(
        "SELECT id, movie_title, movie_year, genre, era, mood_tags, occasion_tags, description, why_fits"
        " FROM scenes WHERE status='approved'").fetchall()
    ids, texts = [], []
    for r in rows:
        import json as _json
        moods = " ".join(_json.loads(r["mood_tags"] or "[]"))
        occ = " ".join(_json.loads(r["occasion_tags"] or "[]"))
        text = " ".join(str(x) for x in [r["movie_title"], r["movie_year"], r["genre"], r["era"],
                                          moods, occ, r["description"], r["why_fits"]] if x)
        ids.append(r["id"])
        texts.append(text)
    return ids, texts


def semantic_search(conn, query: str, limit: int = 10) -> list[dict]:
    """Возвращает [{scene_id, score}] по косинусной близости к запросу."""
    global _cache
    with _lock:
        ids, texts = _corpus(conn)
        n = len(ids)
        if n == 0:
            return []
        if _cache["n"] != n or _cache["mat"] is None:
            vec = TfidfVectorizer(stop_words=RU_STOPWORDS, ngram_range=(1, 2), min_df=1)
            _cache["mat"] = vec.fit_transform(texts)
            _cache["vec"] = vec
            _cache["ids"] = ids
            _cache["n"] = n
        vec = _cache["vec"]
        q = vec.transform([query])
        sims = cosine_similarity(q, _cache["mat"]).flatten()
        order = sims.argsort()[::-1]
        out = []
        for i in order:
            if sims[i] <= 0:
                break
            out.append({"scene_id": _cache["ids"][i], "score": round(float(sims[i]), 4)})
            if len(out) >= limit:
                break
        if not out:
            # все скоры нулевые (запрос из стоп-слов) — берём топ по сырому ранжированию
            for i in order[: min(limit, 5)]:
                out.append({"scene_id": _cache["ids"][i], "score": round(float(sims[i]), 4)})
        return out
