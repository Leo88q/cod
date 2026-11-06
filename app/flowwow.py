"""Поисковые ссылки «где смотреть» и партнёрная ссылка заказа букета."""
from urllib.parse import quote, urlencode

SERVICE_SEARCH = {
    "vkvideo": "https://vkvideo.ru/search?query={q}",
    "rutube": "https://rutube.ru/search/?query={q}",
}

ALL_SERVICES = ["vkvideo", "rutube"]


def watch_links(scene: dict) -> list[dict]:
    """Куда вести пользователя смотреть сцену: VK Видео + Rutube."""
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
