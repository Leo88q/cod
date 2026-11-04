"""Decode and re-encode uploads; never publish original image/audio bytes.

Not antivirus. Audio subprocess accepts only stdin/pipe protocols (no network or
filesystem references), has a timeout and normalizes to audio-only MP3.
"""
import io
import os
import subprocess
import warnings
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageOps, UnidentifiedImageError
from fastapi import HTTPException

IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
AUDIO = {".ogg", ".oga", ".mp3", ".m4a", ".wav", ".webm"}
Image.MAX_IMAGE_PIXELS = 20_000_000


def _normalize(path, extension):
    source = Path(path)
    target = source.with_suffix(".jpg" if extension in IMAGES else ".mp3")
    temporary = Path(str(target) + ".normalized")
    try:
        if extension in IMAGES:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(source) as picture:
                    picture.load()
                    picture = ImageOps.exif_transpose(picture)
                    picture.thumbnail((2048, 2048))
                    # A fresh canvas strips EXIF, comments, profiles and animation.
                    canvas = Image.new("RGB", picture.size, "white")
                    if "A" in picture.getbands():
                        canvas.paste(picture.convert("RGB"), mask=picture.getchannel("A"))
                    else:
                        canvas.paste(picture.convert("RGB"))
                    canvas.save(temporary, format="JPEG", quality=88)
        elif extension in AUDIO:
            command = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                       "-nostdin", "-protocol_whitelist", "pipe", "-i", "pipe:0",
                       "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1",
                       "-t", "300", "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame",
                       "-b:a", "48k", "-f", "mp3", "pipe:1"]
            result = subprocess.run(command, input=source.read_bytes(), stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=25, check=True)
            if not result.stdout:
                raise ValueError("Empty audio")
            temporary.write_bytes(result.stdout)
        else:
            raise ValueError("Unsupported format")
        os.replace(temporary, target)
        if source != target:
            source.unlink(missing_ok=True)
        return "/media/" + target.name
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning,
            OSError, ValueError, subprocess.SubprocessError):
        temporary.unlink(missing_ok=True)
        source.unlink(missing_ok=True)
        raise HTTPException(400, "Файл повреждён или не содержит поддерживаемое фото/аудио")


# Bound CPU/subprocess pressure per API process. No unbounded conversion queue.
from threading import BoundedSemaphore
_slots = BoundedSemaphore(2)


def normalize(path, extension):
    if not _slots.acquire(blocking=False):
        Path(path).unlink(missing_ok=True)
        raise HTTPException(429, "Обработка файлов занята. Повторите загрузку позже.", headers={"Retry-After": "5"})
    try:
        return _normalize(path, extension)
    finally:
        _slots.release()
