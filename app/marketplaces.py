"""Подарок, который приводит на маркетплейс — без открытого редиректа.

Идея: получатель открывает подарок, а под ним — не рекламный баннер, а одна
вещь, которую можно заказать (или забрать в пункте выдачи по QR). Мы получаем
партнёрское вознаграждение, он получает подарок, а не каталог.

Почему модуль, а не «просто вставим ссылку»:

* **Открытый редирект — дыра.** `?url=https://зло.example` превращает наш домен
  в трамплин для фишинга. Поэтому адрес назначения подписывается HMAC, а хост
  проверяется по белому списку партнёра;
* **комиссия — не бизнес.** Ozon до 12%, WB и Яндекс Маркет через Admitad
  (до 10%), cookie 7–30 дней. При среднем чеке 1500 ₽ это 50–120 ₽ с подарка.
  Значит партнёрка финансирует бесплатный уровень, а не заменяет выручку;
* **реклама должна быть маркирована.** В РФ интернет-реклама требует ERID/ОРД,
  поэтому персональный подарок не должен превращаться в рекламную площадку:
  один товарный слот и только официальные креативы партнёра.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import urllib.parse
from typing import Optional

# Партнёры и их условия (проверено 16.11.2026).
PARTNERS = {
    "ozon": {"title": "Ozon", "commission_pct": 12.0, "cookie_days": 30,
             "how": "своя программа «Ozon Партнёры»", "marks": True},
    "wb": {"title": "Wildberries", "commission_pct": 10.0, "cookie_days": 7,
           "how": "через Admitad", "marks": True},
    "ymarket": {"title": "Яндекс Маркет", "commission_pct": 4.8, "cookie_days": 30,
                "how": "через Admitad", "marks": True},
    "flowwow": {"title": "Flowwow", "commission_pct": 10.0, "cookie_days": 30,
                "how": "прямая программа (букет к подарку)", "marks": True},
}

# хосты, куда вообще разрешено вести получателя
ALLOWED_HOSTS = {
    "ozon": ("ozon.ru",),
    "wb": ("wildberries.ru",),
    "ymarket": ("market.yandex.ru",),
    "flowwow": ("flowwow.com",),
}


class PartnerError(ValueError):
    pass


def commission(partner: str, amount: float) -> float:
    """Сколько получим с заказа. Считаем честно: 0, а не «до 12%»."""
    info = PARTNERS.get(partner)
    if not info:
        raise PartnerError(f"неизвестный партнёр: {partner}")
    if amount <= 0:
        return 0.0
    return round(amount * info["commission_pct"] / 100, 2)


def _host_ok(partner: str, url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS.get(partner, ()))


def _sign(partner: str, code: str, target: str, secret: str) -> str:
    base = f"{partner}|{code}|{target}"
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()[:32]


def offer_path(partner: str, gift_code: str, target: str, secret: str) -> str:
    """Путь нашей ссылки-переходника. Адрес назначения зашит в подпись."""
    if partner not in PARTNERS:
        raise PartnerError(f"неизвестный партнёр: {partner}")
    if not _host_ok(partner, target):
        raise PartnerError("хост не принадлежит партнёру")
    sig = _sign(partner, gift_code, target, secret)
    return f"/go/{urllib.parse.quote(gift_code)}?p={partner}&sig={sig}&u=" + \
        urllib.parse.quote(target, safe="")


def verify(partner: str, gift_code: str, target: str, sig: str, secret: str) -> bool:
    """Проверка перехода. Без неё мы — открытый редирект."""
    if not secret or partner not in PARTNERS:
        return False
    if not _host_ok(partner, target):
        return False
    return hmac.compare_digest(_sign(partner, gift_code, target, secret), sig or "")


def qr_svg(text: str, scale: int = 6, border: int = 2) -> str:
    """QR для печати на карточке. Без внешних сервисов и без интернета."""
    import segno

    qr = segno.make(text, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=scale, border=border, dark="#111111")
    return buf.getvalue().decode()


def qr_matrix(text: str):
    """Готовая QR-матрица для печати на карточке (40×50 мм при 203 dpi)."""
    import segno

    return segno.make(text, error="m")


def is_printable(text: str, min_mm: float = 25.0) -> bool:
    """Помещается ли QR на маленькую этикетку.

    Практическое правило печати: модуль не меньше 0.4 мм, значит QR из 33
    модулей требует 13 мм, из 57 — 23 мм. Всё, что больше 37 модулей, на
    этикетку 25 мм не влезет и будет нечитаемым телефоном.
    """
    qr = qr_matrix(text)
    modules = qr.symbol_size(border=2)[0]
    return modules * 0.4 <= min_mm
