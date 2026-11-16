"""Сервисная обёртка «вашего кино» для API.

Разделяет ответственность: `scripts/make_film.py` умеет только рендер
(чистые функции, легко тестировать), а здесь — путь к файлу в /media,
лимит одновременных сборок и выдача подписанной ссылки.
"""
import os
import sys

from . import config
from .media_access import signed_url

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import make_film  # noqa: E402

# Две одновременные сборки на процесс: ffmpeg+CPU дорогой, очередь без границ
# на API-процессе недопустима (та же политика, что в app/uploads.py).
from threading import BoundedSemaphore  # noqa: E402

_slots = BoundedSemaphore(2)


def build_from_media(photos: list[str], captions: list[str], title: str,
                     voice: str | None, media_dir: str) -> dict:
    """Собрать фильм из уже проверенных путей и вернуть ссылку на результат."""
    if not _slots.acquire(blocking=False):
        raise ValueError("Сборка видео занята — повторите через минуту")
    try:
        frames = [make_film.Frame(p, captions[i] if i < len(captions) else "")
                  for i, p in enumerate(photos)]
        os.makedirs(media_dir, exist_ok=True)
        from secrets import token_urlsafe
        name = "film_" + token_urlsafe(8).replace("-", "").replace("_", "") + ".mp4"
        out_path = os.path.join(media_dir, name)
        meta = make_film.build(frames, out_path, title=title, voice=voice)
        # фильм показываем через подписанную ссылку, как остальное медиа
        meta["url"] = signed_url("/media/" + name, "film")
        return meta
    finally:
        _slots.release()
