"""Image helpers built on Pillow — load / crop / encode, all headless-testable.

The GUI layers (region selector, annotation, HUD) render with GdkPixbuf/cairo,
but every operation that produces the *final* artefact funnels through here so it
can be unit-tested without a display.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from .geometry import Rect


def load(path: str | Path) -> Image.Image:
    img = Image.open(path)
    img.load()
    return img.convert("RGBA")


def crop(img: Image.Image, rect: Rect) -> Image.Image:
    full = Rect(0, 0, img.width, img.height)
    r = rect.clamp(full)
    if r.is_empty():
        raise ValueError("empty crop rectangle")
    return img.crop((r.x, r.y, r.x1, r.y1))


def to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def to_jpg_bytes(img: Image.Image, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def encode(img: Image.Image, fmt: str = "png") -> bytes:
    return to_jpg_bytes(img) if fmt.lower() in ("jpg", "jpeg") else to_png_bytes(img)


def save(img: Image.Image, path: str | Path, fmt: str = "png") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(encode(img, fmt))
    return path
