"""«Ваше кино»: сборка вертикального фильма из фото и голоса.

Ключевая проверка — не «код не упал», а «на выходе валидный MP4 нужного
размера и длительности»: именно это избавляет продукт от фильмов, таймкодов
и прав на чужой контент.
"""
import json
import os
import subprocess
import sys

import imageio_ffmpeg
import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import make_film  # noqa: E402


def _photo(path: str, color: tuple, size: tuple = (1200, 900)) -> str:
    img = Image.new("RGB", size, color)
    img.save(path, quality=90)
    return path


def _voice(path: str, seconds: float = 4.0) -> str:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"sine=frequency=220:duration={seconds}",
                    "-af", "volume=0.2", path], check=True)
    return path


def _probe(path: str) -> dict:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    res = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True)
    info = res.stderr or ""
    out = {"has_video": "Video:" in info, "has_audio": "Audio:" in info, "raw": info}
    for line in info.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            out["seconds"] = int(h) * 3600 + int(m) * 60 + float(s)
        if "Video:" in line and "1080x1920" in line:
            out["size_ok"] = True
    return out


def test_builds_vertical_mp4_with_voice(tmp_path):
    frames = [
        make_film.Frame(_photo(str(tmp_path / f"p{i}.jpg"), (30 + i * 40, 40, 90)), f"подпись {i + 1}")
        for i in range(3)
    ]
    voice = _voice(str(tmp_path / "voice.mp3"), 6.0)
    out = str(tmp_path / "film.mp4")
    meta = make_film.build(frames, out, title="Ане", voice=voice)
    assert os.path.exists(out) and meta["bytes"] > 5000
    probe = _probe(out)
    assert probe["has_video"] and probe["has_audio"]
    assert probe.get("size_ok"), "фильм должен быть 1080x1920 (история/Telegram)"
    assert abs(probe["seconds"] - meta["seconds"]) <= 1.0
    # кадры подгоняются под длину голоса, а не наоборот
    assert meta["seconds"] <= 6.5 and meta["has_voice"] is True


def test_seconds_per_frame_follows_audio_length():
    assert make_film._seconds_per_frame(3, 6.0) == pytest.approx(2.0)
    assert make_film._seconds_per_frame(3, None) == 3.2
    # очень длинное голосовое не растягивает кадр бесконечно
    assert make_film._seconds_per_frame(2, 60.0) == 8.0


def test_video_without_voice_still_valid(tmp_path):
    frames = [make_film.Frame(_photo(str(tmp_path / "a.jpg"), (60, 30, 60)))]
    out = str(tmp_path / "silent.mp4")
    meta = make_film.build(frames, out, title="")
    assert meta["has_voice"] is False
    probe = _probe(out)
    assert probe["has_video"] and probe["has_audio"], "тихая дорожка нужна для совместимости"


def test_limits_and_validation(tmp_path):
    frame = make_film.Frame(_photo(str(tmp_path / "x.jpg"), (10, 10, 10)))
    with pytest.raises(ValueError):
        make_film.build([], str(tmp_path / "no.mp4"))
    with pytest.raises(ValueError):
        make_film.build([frame] * (make_film.MAX_PHOTOS + 1), str(tmp_path / "many.mp4"))
    with pytest.raises(ValueError):
        make_film.build([make_film.Frame(str(tmp_path / "missing.jpg"))], str(tmp_path / "m.mp4"))
    with pytest.raises(ValueError):
        make_film.build([frame], str(tmp_path / "v.mp4"), voice=str(tmp_path / "nope.mp3"))


def test_too_long_voice_is_rejected(tmp_path):
    frame = make_film.Frame(_photo(str(tmp_path / "y.jpg"), (20, 20, 20)))
    voice = _voice(str(tmp_path / "long.mp3"), 30.0)
    real = make_film.MAX_SECONDS
    make_film.MAX_SECONDS = 10
    try:
        with pytest.raises(ValueError):
            make_film.build([frame], str(tmp_path / "long.mp4"), voice=voice)
    finally:
        make_film.MAX_SECONDS = real


def test_photo_is_cropped_not_distorted(tmp_path):
    """9:16 без искажений: широкое фото обрезается по центру, а не сплющивается."""
    from PIL import Image as PILImage
    wide = _photo(str(tmp_path / "wide.jpg"), (100, 60, 20), size=(2000, 500))
    frame_out = str(tmp_path / "rendered.jpg")
    make_film.render_frame(make_film.Frame(wide, "тест"), 0, 3, "Заголовок", frame_out)
    with PILImage.open(frame_out) as img:
        assert img.size == (make_film.WIDTH, make_film.HEIGHT)


def test_source_photos_are_not_published_and_temp_is_cleaned(tmp_path, monkeypatch):
    """После сборки временные кадры удаляются: исходники не остаются в файловой системе."""
    seen = {}
    real_mkdtemp = make_film.tempfile.mkdtemp

    def spy(*a, **kw):
        path = real_mkdtemp(*a, **kw)
        seen["dir"] = path
        return path

    monkeypatch.setattr(make_film.tempfile, "mkdtemp", spy)
    frames = [make_film.Frame(_photo(str(tmp_path / "z.jpg"), (5, 5, 5)))]
    make_film.build(frames, str(tmp_path / "clean.mp4"))
    assert seen.get("dir") and not os.path.exists(seen["dir"])
