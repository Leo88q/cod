"""Таймкоды без субтитров: поиск монтажных склеек прямо по картинке.

Зачем: субтитры — чужой контент, их нельзя легально автоматизировать на
масштабе. Но «где в фильме начинается новый момент» можно узнать из самого
видео, которое мы имеем право показывать: своё «Ваше кино», общественное
достояние, официальные каналы.

Как: читаем видео уменьшенным в 64×36 RGB на 10 к/с (копейки по CPU и памяти),
считаем среднюю разницу соседних кадров и ищем резкие локальные выбросы.
Порог не магическое число: пик должен быть в `ratio` раз выше медианы
окрестности, иначе статичный кадр с шумом даёт ложные срабатывания
(проверено: в статике медиана и MAD равны нулю, любой абсолютный порог ломается).

Без нейросетей, без внешних сервисов, только ffmpeg + numpy.
"""
from __future__ import annotations

import subprocess
from typing import List, Optional

import numpy as np

WIDTH, HEIGHT, FPS = 64, 36, 10


def _frames(path: str, fps: int = FPS) -> np.ndarray:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    raw = subprocess.run(
        [ffmpeg, "-v", "error", "-i", path, "-vf", f"scale={WIDTH}:{HEIGHT}",
         "-pix_fmt", "rgb24", "-r", str(fps), "-f", "rawvideo", "-"],
        capture_output=True, check=False, timeout=600,
    ).stdout
    if not raw:
        return np.zeros((0, HEIGHT, WIDTH, 3), dtype=np.float32)
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, HEIGHT, WIDTH, 3).astype(np.float32)


def cuts(path: str, *, fps: int = FPS, ratio: float = 8.0,
         floor: float = 1.0, merge: float = 0.35) -> List[float]:
    """Вернуть времена монтажных склеек в секундах.

    `ratio` — во сколько раз пик должен превышать медиану окрестности;
    `floor` — абсолютный минимум разницы (отсекает шум сжатия);
    `merge` — склейки ближе этого расстояния считаются одной.
    """
    frames = _frames(path, fps)
    if len(frames) < 5:
        return []
    diff = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2, 3))
    if float(diff.max()) < floor:
        return []
    out: List[float] = []
    for i in range(1, len(diff) - 1):
        if diff[i] < floor:
            continue
        if not (diff[i] >= diff[i - 1] and diff[i] >= diff[i + 1]):
            continue
        lo, hi = max(0, i - 7), min(len(diff), i + 8)
        neighbours = np.concatenate([diff[lo:i], diff[i + 1:hi]])
        if diff[i] > ratio * (float(np.median(neighbours)) + 0.05):
            t = round((i + 1) / fps, 2)
            if not out or t - out[-1] > merge:
                out.append(t)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Найти монтажные склейки в видео")
    parser.add_argument("video")
    parser.add_argument("--ratio", type=float, default=8.0)
    args = parser.parse_args(argv)
    found = cuts(args.video, ratio=args.ratio)
    for t in found:
        print(f"{t:7.2f} с")
    print(f"итого: {len(found)} склеек")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
