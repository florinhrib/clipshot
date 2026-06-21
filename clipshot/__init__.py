"""ClipShot — a CleanShot-class screenshot tool for Linux / Wayland (GNOME first).

Architecture in one breath:
  hotkey/mouse -> capture full screen (portal or shell-ext) -> region select on the
  frozen still -> crop -> auto-copy to clipboard -> floating HUD -> optional annotate,
  pin, OCR, history.  Tray-only, no main window.

This package is intentionally split so each layer is independently testable:
  config, clipboard, capture, geometry have zero GUI deps.
"""

__version__ = "0.1.0"
APP_ID = "uk.florinlab.ClipShot"
APP_NAME = "ClipShot"
