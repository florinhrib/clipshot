"""OCR via Tesseract subprocess — headless, no GTK dependency.

Pipes a PIL image as PNG into ``tesseract stdin stdout`` and returns the
extracted text.  Tesseract must be installed separately (see RuntimeError
message for the install hint).  The function is fully testable without a
display and without the extension.
"""
from __future__ import annotations

import shutil
import subprocess

from PIL import Image

from .imaging import to_png_bytes


def tesseract_available() -> bool:
    """Return True if the ``tesseract`` binary is on PATH."""
    return shutil.which("tesseract") is not None


def extract_text(image: Image.Image, lang: str = "eng") -> str:
    """Extract text from *image* using Tesseract OCR.

    Pipes the image as PNG to ``tesseract stdin stdout -l <lang> --psm 6``
    and returns the stripped output text.

    Raises:
        RuntimeError: if tesseract is not installed.
        subprocess.CalledProcessError: if tesseract exits non-zero.
    """
    if shutil.which("tesseract") is None:
        raise RuntimeError(
            "tesseract not installed — run: sudo dnf install tesseract tesseract-langpack-eng"
        )

    png_data = to_png_bytes(image)
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", lang, "--psm", "6"],
        input=png_data,
        capture_output=True,
        check=True,
    )
    return result.stdout.decode("utf-8", errors="replace").strip()
