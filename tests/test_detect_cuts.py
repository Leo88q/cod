"""Таймкоды без субтитров: детектор склеек по картинке.

Проверяем на сгенерированном видео с известными границами: ложных срабатываний
быть не должно, иначе «сцена» окажется посреди статичного кадра.
"""
import subprocess

import pytest

from app import config  # noqa: F401  (фикстуры окружения)
from scripts import detect_cuts


def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


@pytest.fixture()
def video(tmp_path):
    def shot(name, color, seconds):
        out = tmp_path / f"{name}.mp4"
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"color=c={color}:s=320x568:d={seconds}", "-r", "25",
                        "-pix_fmt", "yuv420p", str(out)], check=True)
        return out

    parts = [shot("a", "red", 3), shot("b", "green", 3), shot("c", "blue", 3)]
    concat = tmp_path / "list.txt"
    concat.write_text("".join(f"file '{p}'\n" for p in parts))
    movie = tmp_path / "movie.mp4"
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(concat), "-c", "copy", str(movie)], check=True)
    return movie


def test_finds_known_cuts(video):
    found = detect_cuts.cuts(str(video))
    assert len(found) == 2
    for got, expected in zip(found, (3.0, 6.0)):
        assert abs(got - expected) < 0.5


def test_static_video_has_no_cuts(video, tmp_path):
    still = tmp_path / "still.mp4"
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=gray:s=320x568:d=4", "-r", "25",
                    "-pix_fmt", "yuv420p", str(still)], check=True)
    assert detect_cuts.cuts(str(still)) == []


def test_broken_file_returns_empty(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    assert detect_cuts.cuts(str(bad)) == []


def test_single_shot_has_no_cuts(video, tmp_path):
    only = tmp_path / "one.mp4"
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=blue:s=320x568:d=3", "-r", "25",
                    "-pix_fmt", "yuv420p", str(only)], check=True)
    assert detect_cuts.cuts(str(only)) == []
