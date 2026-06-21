import json

from PIL import Image

from clipshot import imaging
from clipshot.config import Config
from clipshot.geometry import Rect


def _solid(w, h, color):
    return Image.new("RGBA", (w, h), color)


def test_crop_and_encode():
    img = _solid(100, 100, (255, 0, 0, 255))
    out = imaging.crop(img, Rect(10, 10, 40, 30))
    assert out.size == (40, 30)
    png = imaging.to_png_bytes(out)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_crop_clamps_to_image():
    img = _solid(50, 50, (0, 255, 0, 255))
    out = imaging.crop(img, Rect(40, 40, 100, 100))
    assert out.size == (10, 10)


def test_encode_jpg():
    img = _solid(20, 20, (1, 2, 3, 255))
    data = imaging.encode(img, "jpg")
    assert data[:3] == b"\xff\xd8\xff"


def test_config_roundtrip(tmp_path):
    cfg = Config()
    cfg["hotkey_region"] = "<Control>F12"
    cfg["dim_opacity"] = 0.6
    p = tmp_path / "config.json"
    cfg.save(p)
    loaded = Config.load(p)
    assert loaded["hotkey_region"] == "<Control>F12"
    assert loaded["dim_opacity"] == 0.6
    # defaults still present
    assert loaded["copy_to_clipboard"] is True


def test_config_ignores_unknown_keys(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"bogus": 1, "ocr_lang": "deu"}))
    cfg = Config.load(p)
    assert cfg["ocr_lang"] == "deu"
    assert "bogus" not in cfg.as_dict()


def test_config_load_missing_returns_defaults(tmp_path):
    cfg = Config.load(tmp_path / "nope.json")
    assert cfg["image_format"] == "png"
