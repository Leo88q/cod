#!/usr/bin/env python3
"""«Ваше кино»: вертикальный фильм из фотографий, голоса и титров.

Собирает MP4 1080×1920 (9:16, как в истории/Telegram) из 3–10 фото:
каждому кадру — подпись, мягкий затемнённый низ под текст, плавное появление,
поверх — голосовое или музыка. Никакого чужого контента: только то, что
прислал пользователь.

Зачем отдельный скрипт, а не фильмы из кинотеки:
  * нет прав на чужие кадры вообще;
  * нет проблемы «фильма нет в плеере» — видео получается своё,
    его больше нет ни у кого;
  * нет зависимости от таймкодов, изданий и сдвигов версий.

Безопасность и пределы (те же принципы, что в app/uploads.py):
  * локальные файлы только, никаких сетевых входов ffmpeg;
  * все входы пересобираются (кадры рендерятся заново из фото), исходники
    не публикуются;
  * ограничены число кадров, длительность и таймаут ffmpeg;
  * временные файлы удаляются всегда.

CLI:
    python scripts/make_film.py \
      --photo a.jpg --photo b.jpg --photo c.jpg \
      --caption "как мы познакомились" --caption "первое свидание" --caption "и вот мы здесь" \
      --title "Ане" --voice voice.mp3 \
      --out /tmp/your_film.mp4
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

WIDTH, HEIGHT = 1080, 1920
MAX_PHOTOS = 10
MAX_SECONDS = 90
FFMPEG_TIMEOUT = 180
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/DejaVuSans.ttf",
)


@dataclass
class Frame:
    path: str
    caption: str = ""


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _fit(img: Image.Image) -> Image.Image:
    """Вписать фото в 9:16 без искажений: масштаб по большей стороне + центральный кроп."""
    img = img.convert("RGB")
    scale = max(WIDTH / img.width, HEIGHT / img.height)
    new = (max(WIDTH, int(img.width * scale + 0.5)), max(HEIGHT, int(img.height * scale + 0.5)))
    img = img.resize(new, Image.LANCZOS)
    left = (img.width - WIDTH) // 2
    top = (img.height - HEIGHT) // 3  # чуть выше центра: лица обычно сверху
    return img.crop((left, top, left + WIDTH, top + HEIGHT))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        probe = f"{line} {w}".strip()
        if draw.textlength(probe, font=font) <= max_width:
            line = probe
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def render_frame(frame: Frame, index: int, total: int, title: str, out_path: str) -> None:
    with Image.open(frame.path) as src:
        img = _fit(src)
    # немного притушить фото, чтобы текст читался
    img = ImageEnhance.Brightness(img).enhance(0.82)
    draw = ImageDraw.Draw(img, "RGBA")

    # градиент снизу под подпись
    band = 760
    for i in range(band):
        alpha = int(210 * (i / band) ** 1.4)
        draw.rectangle([0, HEIGHT - band + i, WIDTH, HEIGHT - band + i + 1], fill=(0, 0, 0, alpha))

    title_font = _font(46)
    caption_font = _font(68)
    small_font = _font(34)

    # заголовок сверху (например, имя получателя)
    if title:
        draw.text((60, 80), title, font=title_font, fill=(255, 255, 255, 230))

    # подпись кадра
    if frame.caption:
        lines = _wrap(draw, frame.caption, caption_font, WIDTH - 140)
        y = HEIGHT - 300 - (len(lines) - 1) * 82
        for line in lines:
            draw.text((70, y), line, font=caption_font, fill=(255, 255, 255, 255))
            y += 82

    # индикатор кадров
    if total > 1:
        bar_w = (WIDTH - 140) // total
        for i in range(total):
            color = (255, 255, 255, 235) if i <= index else (255, 255, 255, 90)
            draw.rectangle([70 + i * bar_w, 160, 70 + i * bar_w + bar_w - 10, 168], fill=color)

    draw.text((70, HEIGHT - 110), "сделано с любовью · Сцена", font=small_font,
              fill=(255, 255, 255, 150))
    img.save(out_path, "JPEG", quality=88)


def _seconds_per_frame(photos: int, audio_seconds: float | None) -> float:
    if audio_seconds and audio_seconds > 0:
        # кадры меняются ровно под длину голоса
        return max(1.8, min(8.0, audio_seconds / photos))
    return 3.2


def _audio_seconds(path: str | None) -> float | None:
    if not path:
        return None
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    res = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True,
                         timeout=60)
    for line in (res.stderr or "").splitlines():
        if "Duration:" in line:
            raw = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = raw.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return None


def build(frames: list[Frame], out_path: str, title: str = "",
          voice: str | None = None, fade: bool = True) -> dict:
    """Собрать фильм. Возвращает метаданные (длительность, число кадров, размер)."""
    if not frames:
        raise ValueError("нужен хотя бы один кадр")
    if len(frames) > MAX_PHOTOS:
        raise ValueError(f"максимум {MAX_PHOTOS} кадров")
    for f in frames:
        if not os.path.exists(f.path):
            raise ValueError(f"нет файла: {f.path}")
    if voice and not os.path.exists(voice):
        raise ValueError(f"нет аудио: {voice}")

    audio_len = _audio_seconds(voice)
    if audio_len and audio_len > MAX_SECONDS:
        raise ValueError(f"аудио длиннее {MAX_SECONDS} сек — сократи голосовое")
    per = _seconds_per_frame(len(frames), audio_len)
    total = round(per * len(frames), 2)

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    workdir = tempfile.mkdtemp(prefix="your-film-")
    try:
        rendered = []
        for i, frame in enumerate(frames):
            path = os.path.join(workdir, f"f{i:02d}.jpg")
            render_frame(frame, i, len(frames), title, path)
            rendered.append(path)

        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        for path in rendered:
            cmd += ["-loop", "1", "-t", str(per), "-i", path]
        if voice:
            cmd += ["-i", voice]
        else:
            cmd += ["-f", "lavfi", "-t", str(total), "-i", "anullsrc=r=44100:cl=mono"]

        parts = "".join(f"[{i}:v]scale={WIDTH}:{HEIGHT},setsar=1,fps=25[v{i}];"
                        for i in range(len(rendered)))
        concat = "".join(f"[v{i}]" for i in range(len(rendered)))
        graph = f"{parts}{concat}concat=n={len(rendered)}:v=1:a=0[vc]"
        if fade and total > 2:
            graph += f";[vc]fade=t=in:st=0:d=0.6,fade=t=out:st={max(0.0, total - 0.8):.2f}:d=0.8[vout]"
            vlabel = "vout"
        else:
            vlabel = "vc"

        cmd += ["-filter_complex", graph, "-map", f"[{vlabel}]",
                "-map", f"{len(rendered)}:a", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-shortest", "-movflags", "+faststart", out_path]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg: {(res.stderr or '').strip()[:500]}")
        return {
            "path": out_path,
            "frames": len(rendered),
            "seconds": total,
            "seconds_per_frame": round(per, 2),
            "bytes": os.path.getsize(out_path),
            "has_voice": bool(voice),
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Собрать вертикальный фильм из фото")
    ap.add_argument("--photo", action="append", required=True, help="фото (3–10 штук)")
    ap.add_argument("--caption", action="append", default=[], help="подпись к кадру (по порядку)")
    ap.add_argument("--title", default="", help="заголовок сверху (например, имя)")
    ap.add_argument("--voice", default=None, help="голосовое/музыка (mp3/m4a/wav)")
    ap.add_argument("--out", default="your_film.mp4")
    ap.add_argument("--no-fade", action="store_true")
    args = ap.parse_args()

    captions = list(args.caption)
    frames = [Frame(p, captions[i] if i < len(captions) else "")
              for i, p in enumerate(args.photo)]
    meta = build(frames, args.out, title=args.title, voice=args.voice, fade=not args.no_fade)
    print(f"Готово: {meta['path']} · {meta['seconds']}с · {meta['frames']} кадров · "
          f"{meta['bytes'] / 1024:.0f} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
