"""User configuration — JSON at ~/.config/clipshot/config.json.

Loaded once at daemon start; the settings UI writes through here.  Kept GUI-free
so it is unit-testable and importable from the CLI.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "clipshot"
CONFIG_PATH = CONFIG_DIR / "config.json"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "clipshot"
HISTORY_DIR = DATA_DIR / "history"

DEFAULTS: dict[str, Any] = {
    # capture
    "copy_to_clipboard": True,          # image on clipboard the instant you release
    "save_to_disk": False,              # also write a file
    "save_dir": str(Path.home() / "Pictures" / "Screenshots"),
    "filename_template": "ClipShot {date} {time}",
    "image_format": "png",              # png | jpg
    "freeze_screen": True,              # crop a frozen still (inherent in our pipeline)
    "hide_cursor": True,
    "self_timer_seconds": 0,            # 0 = off; used by --capture-region --timer
    # region selector UX
    "show_magnifier": True,
    "show_crosshair": True,
    "show_dimensions": True,
    "selection_color": "#3584e4",       # accent
    "dim_opacity": 0.45,
    # post-capture HUD
    "show_hud": True,
    "hud_corner": "bottom-left",        # bottom-left|bottom-right|top-left|top-right
    "hud_autoclose_seconds": 0,         # 0 = never
    "hud_autoclose_action": "discard",  # discard|save
    # backends
    "capture_backend": "auto",          # auto|extension|portal
    "clipboard_backend": "auto",        # auto|wayland|x11
    # power features
    "ocr_lang": "eng",
    "history_enabled": True,
    "history_max_items": 200,
    "pin_shadow": True,
    "pin_rounded": True,
    # hotkeys (registered into GNOME at install/apply time)
    "hotkey_region": "<Super><Shift>s",
    "hotkey_fullscreen": "<Super><Shift>f",
    "hotkey_window": "<Super><Shift>w",
    "hotkey_ocr": "<Super><Shift>t",
    "hotkey_previous": "<Super><Shift>r",
}


class Config:
    def __init__(self, data: dict[str, Any] | None = None):
        self._data = dict(DEFAULTS)
        if data:
            self._data.update({k: v for k, v in data.items() if k in DEFAULTS})

    # dict-ish access -----------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def update(self, **kw: Any) -> None:
        for k, v in kw.items():
            if k in DEFAULTS:
                self._data[k] = v

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)

    # persistence ---------------------------------------------------------
    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return cls(json.load(fh))
        except (FileNotFoundError, json.JSONDecodeError):
            return cls()

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)
        os.replace(tmp, path)  # atomic
