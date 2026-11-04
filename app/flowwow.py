"""Интеграционный слой агрегаторов цветов (Flowwow и др.).

Механики (см. docs/02-flowwow-playbook.md):
- витрина букетов — наш кэш ссылок агрегатора (таблица bouquets);
- заказ — deep link на товар агрегатора с нашим ref/utm: заказ идёт по прямой ссылке
  магазина, комиссия Flowwow = 0%, а комиссию нам платит магазин по агентскому договору;
- «смотреть сцену» — ссылки поиска в легальных плеерах (+ прямой watch_url, если проверен).
"""
from urllib.parse import quote, urlencode

# Поиск фильма в легальных плеерах (service -> шаблон URL)
SERVICE_SEARCH = {
    "kinopoisk": "https://www.kinopoisk.ru/index.php?kp_query={q}",
    "ivi": "https://www.ivi.ru/search/?q={q}",
    "okko": "https://okko.tv/search?query={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "vkvideo": "https://vkvideo.ru/search?query={q}",
}

ALL_SERVICES = ["kinopoisk", "ivi", "okko", "youtube"]


def watch_links(scene: dict) -> list[dict]:
    """Куда вести пользователя смотреть сцену. Приоритет — проверенному watch_url."""
    out = []
    if scene.get("watch_url"):
        out.append({"service": scene.get("service") or "плеер", "url": scene["watch_url"], "direct": True})
    q = quote(f'{scene.get("movie_title", "")} {scene.get("movie_year") or ""}'.strip())
    preferred = scene.get("service")
    services = ([preferred] if preferred in SERVICE_SEARCH else []) + \
               [s for s in ALL_SERVICES if s != preferred]
    for s in services:
        out.append({"service": s, "url": SERVICE_SEARCH[s].format(q=q), "direct": False})
    return out


def order_link(bouquet: dict, gift_id: str, ref_code: str) -> str:
    """Ссылка на букет у агрегатора с нашей партнёрской меткой."""
    url = bouquet["flowwow_url"]
    params = {
        "utm_source": "scena",
        "utm_medium": "gift_page",
        "utm_campaign": f"gift_{gift_id}",
        "ref": ref_code,
    }
    sep = "&" if "?" in url else "?"
    return url + sep + urlencode(params)
