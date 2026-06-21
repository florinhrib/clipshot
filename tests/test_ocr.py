"""Tests for clipshot.ocr — headless, no display required."""
from __future__ import annotations

import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont

from clipshot.ocr import extract_text, tesseract_available


# ---------------------------------------------------------------------------
# Always-run test: missing tesseract raises a clear error
# ---------------------------------------------------------------------------

def test_extract_text_raises_when_tesseract_absent(monkeypatch) -> None:
    """extract_text must raise RuntimeError with install hint when tesseract is absent."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    img = Image.new("RGB", (100, 30), color=(255, 255, 255))
    with pytest.raises(RuntimeError, match="tesseract not installed"):
        extract_text(img)


# ---------------------------------------------------------------------------
# Tests that require tesseract to be installed
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not tesseract_available(), reason="tesseract not installed")
def test_extract_text_hello() -> None:
    """Render 'HELLO' in black on white and verify tesseract reads it back."""
    img = Image.new("RGB", (200, 60), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Use the default PIL font (always available).
    draw.text((10, 10), "HELLO", fill=(0, 0, 0))

    text = extract_text(img)
    assert "HELLO" in text.upper(), f"Expected 'HELLO' in OCR output, got: {text!r}"
