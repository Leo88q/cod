"""Авторизация в Mini App на разных платформах: Telegram, VK, Одноклассники.

Зачем отдельный модуль: продукт живёт в мессенджерах, а не в одном Telegram.
Логика одна, различается только способ доказать личность:

* **Telegram** — `initData` с HMAC-SHA256 по токену бота (уже есть в security.py);
* **VK** — параметры запуска (`vk_user_id`, `vk_app_id`, `sign`) с подписью
  HMAC-SHA256 по секретному ключу приложения. Подпись обязана проверяться на
  бэкенде: `vk_user_id` из URL доверять нельзя;
* **Одноклассники** — VK Mini Apps в ОК работают через тот же VK Bridge, поэтому
  приложение регистрируется один раз в VK и дополнительно получает витрину ОК.

Провайдер-агностичная модель: у пользователя есть `identity` (провайдер + внешний
id), а не «telegram_id». Telegram-id остаётся основным ключом для совместимости:
существующие пользователи и подарки не должны пострадать.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
import urllib.parse
from typing import Optional

from . import db as db_module
from . import config

PROVIDERS = ("telegram", "vk", "ok")

# поля запуска VK, которые не входят в подпись
VK_IGNORED = ("sign", "hash")


class ProviderError(ValueError):
    """Подпись неверна или параметры запуска неполные."""


# --------------------------------------------------------------------------
# VK / OK
# --------------------------------------------------------------------------
def validate_vk_launch(query: str, secret: str, max_age: int | None = None) -> dict:
    """Проверить подпись параметров запуска VK Mini App.

    `query` — строка `location.search` (например `?vk_user_id=1&vk_app_id=2&sign=…`)
    или уже разобранный query string. Секрет — «защищённый ключ» приложения.

    Каноническая строка: пары `vk_*`, отсортированные по имени, без `sign`,
    конкатенация `k=v` через `&`. HMAC-SHA256 на секрете, результат — base64url
    без паддинга. Возвращает dict проверенных параметров.
    """
    if not secret:
        raise ProviderError("VK_SECRET не настроен")
    raw = query[1:] if query.startswith("?") else query
    pairs = urllib.parse.parse_qsl(raw, keep_blank_values=True)
    parsed = dict(pairs)
    sign = parsed.pop("sign", None)
    if not sign:
        raise ProviderError("нет подписи запуска")
    parts = sorted((k, v) for k, v in parsed.items() if k.startswith("vk_"))
    if not parts:
        raise ProviderError("нет параметров vk_*")
    base = "&".join(f"{k}={v}" for k, v in parts)
    import base64
    digest = hmac.new(secret.encode(), base.encode(), hashlib.sha256).digest()
    expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    if not hmac.compare_digest(expected, sign):
        raise ProviderError("подпись запуска не совпала")
    try:
        user_id = int(parsed.get("vk_user_id", "0"))
    except ValueError as exc:
        raise ProviderError("vk_user_id не число") from exc
    if not 0 < user_id < 2**63:
        raise ProviderError("некорректный vk_user_id")
    if max_age:
        try:
            issued = int(parsed.get("vk_ts", "0"))
        except ValueError as exc:
            raise ProviderError("vk_ts не число") from exc
        if issued and abs(time.time() - issued) > max_age:
            raise ProviderError("параметры запуска устарели")
    params = dict(parsed)
    params["vk_user_id"] = user_id
    return params


def ok_user_key(ok_user_id: str) -> str:
    """В ОК пользователь приходит как строка; нормализуем в стабильный ключ."""
    if not re.fullmatch(r"\d{1,19}", str(ok_user_id)):
        raise ProviderError("некорректный ok_user_id")
    return str(ok_user_id)


# --------------------------------------------------------------------------
# единая идентичность
# --------------------------------------------------------------------------
def upsert_identity(provider: str, external_id: str, display_name: str = "") -> dict:
    """Найти или создать пользователя по внешней идентичности провайдера.

    Telegram остаётся в `users.telegram_id` (совместимость с подарками и
    существующими запросами). VK/OK пишутся в `user_identities` и связываются
    с тем же `users.id`, поэтому один человек в двух мессенджерах — одна история.
    """
    if provider not in PROVIDERS:
        raise ProviderError(f"неизвестный провайдер: {provider}")
    conn = db_module.connect(config.DB_PATH)
    try:
        if provider == "telegram":
            if not re.fullmatch(r"\d{1,19}", str(external_id)):
                raise ProviderError("некорректный telegram id")
            tid = int(external_id)
            conn.execute(
                "INSERT INTO users (telegram_id, username) VALUES (?, ?)"
                " ON CONFLICT(telegram_id) DO NOTHING",
                (tid, display_name or None))
            row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (tid,)).fetchone()
            conn.commit()
            return dict(row)

        external_id = ok_user_key(external_id) if provider == "ok" else str(external_id)
        found = conn.execute(
            "SELECT u.* FROM user_identities i JOIN users u ON u.id=i.user_id"
            " WHERE i.provider=? AND i.external_id=?", (provider, external_id)).fetchone()
        if found:
            conn.execute("UPDATE user_identities SET last_seen_at=datetime('now')"
                         " WHERE provider=? AND external_id=?", (provider, external_id))
            conn.commit()
            return dict(found)
        # новый пользователь платформы: telegram_id остаётся NULL
        conn.execute("INSERT INTO users (username) VALUES (?)", (display_name or None,))
        user_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            "INSERT INTO user_identities (user_id, provider, external_id, display_name)"
            " VALUES (?,?,?,?)", (user_id, provider, external_id, display_name or None))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def provider_from_request(request) -> Optional[dict]:
    """Определить платформу по заголовкам Mini App и вернуть пользователя.

    Приоритет: Telegram initData (как раньше) -> VK launch params
    (`X-VK-Launch-Params` от клиента или query string) -> ОК.
    """
    from .security import upsert_user_from_init_data

    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if init_data and config.BOT_TOKEN:
        user = upsert_user_from_init_data(init_data)
        if user:
            return {"provider": "telegram", "user": user}

    vk_raw = request.headers.get("X-VK-Launch-Params", "") or request.url.query
    if vk_raw and config.VK_SECRET:
        try:
            params = validate_vk_launch(vk_raw, config.VK_SECRET)
        except ProviderError:
            params = None
        if params:
            name = " ".join(x for x in (params.get("vk_first_name"),
                                        params.get("vk_last_name")) if x)
            return {"provider": "vk", "user": upsert_identity("vk", str(params["vk_user_id"]), name),
                    "params": params}

    ok_id = request.headers.get("X-OK-User-Id", "")
    if ok_id and config.OK_SECRET:
        provided = request.headers.get("X-OK-Sign", "")
        if not _ok_signature_ok(ok_id, provided):
            raise ProviderError("подпись ОК не совпала")
        return {"provider": "ok", "user": upsert_identity("ok", ok_id)}
    return None


def _ok_signature_ok(user_id: str, provided: str) -> bool:
    """ОК отдаёт своих пользователей в VK-совместимом виде; подпись считаем по
    секрету приложения ОК, если он настроен, иначе полагаемся на VK-подпись."""
    if not config.OK_SECRET:
        return False
    base = f"ok_user_id={user_id}"
    digest = hmac.new(config.OK_SECRET.encode(), base.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, provided or "")
