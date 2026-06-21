"""ClipShot daemon — single-instance GTK/Adw application.

The hotkey/mouse binding launches a short-lived `clipshot --capture-region`; GIO's
single-instance machinery routes that as an *action* to the already-running
primary instance, which performs the capture.  Keeping one long-lived process
means the floating wl-copy offer (and the tray) live as long as the session.

Capture flow:
  capture full screen (async, portal/extension) -> RegionSelector on the frozen
  still -> crop -> [auto-copy] -> [save] -> [history] -> floating HUD.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, __version__, capture, clipboard, imaging
from .config import HISTORY_DIR, Config
from .geometry import Rect
from .region_selector import RegionSelector

ACTIONS = {
    "capture-region": "Capture a region",
    "capture-fullscreen": "Capture the whole screen",
    "capture-window": "Capture a window",
    "capture-ocr": "Extract text (OCR)",
    "capture-previous": "Repeat the last region",
    "capture-timer": "Region capture after a delay",
    "show-settings": "Open settings",
    "show-history": "Open capture history",
    "about": "About ClipShot",
    "quit": "Quit",
}


@dataclass
class CaptureResult:
    image: "object"          # PIL.Image
    rect: Rect
    timestamp: float
    saved_path: Path | None = None

    @property
    def png(self) -> bytes:
        return imaging.to_png_bytes(self.image)  # type: ignore[arg-type]


class ClipShotApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.cfg = Config.load()
        self._last_rect: Rect | None = None
        self._busy = False
        self._tray = None
        self.set_option_context_summary("CleanShot-class screenshots for Linux/Wayland")

    # --- lifecycle -------------------------------------------------------
    def do_startup(self):
        Adw.Application.do_startup(self)
        for name in ACTIONS:
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", self._on_action)
            self.add_action(act)
        # The daemon must stay alive with no window open (tray-only app).
        self.hold()
        try:
            from .tray import Tray
            self._tray = Tray(self)
        except Exception as exc:  # tray is best-effort
            print(f"[clipshot] tray unavailable: {exc}", file=sys.stderr)

    def do_activate(self):
        # No-op: we are a background/tray app, no main window.
        pass

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        action = "capture-region"  # default when invoked bare
        timer = 0
        for a in args:
            if a.startswith("--"):
                key = a[2:]
                if key == "daemon":
                    action = ""        # just run the daemon
                elif key == "region":
                    action = "capture-region"
                elif key == "fullscreen":
                    action = "capture-fullscreen"
                elif key == "window":
                    action = "capture-window"
                elif key == "ocr":
                    action = "capture-ocr"
                elif key == "previous":
                    action = "capture-previous"
                elif key == "settings":
                    action = "show-settings"
                elif key == "history":
                    action = "show-history"
                elif key.startswith("timer"):
                    action = "capture-timer"
                    if "=" in a:
                        timer = int(a.split("=", 1)[1])
        self._pending_timer = timer
        if action:
            self.activate_action(action, None)
        return 0

    # --- action dispatch -------------------------------------------------
    def _on_action(self, action, _param):
        name = action.get_name()
        if name == "quit":
            self.quit_app()
        elif name == "about":
            self.show_about()
        elif name == "show-settings":
            self.open_settings()
        elif name == "show-history":
            self.open_history()
        elif name == "capture-region":
            self.capture_region()
        elif name == "capture-timer":
            self.capture_region(timer=getattr(self, "_pending_timer", 0) or self.cfg["self_timer_seconds"])
        elif name == "capture-fullscreen":
            self.capture_fullscreen()
        elif name == "capture-window":
            self.capture_window()
        elif name == "capture-ocr":
            self.capture_ocr()
        elif name == "capture-previous":
            self.capture_previous()

    # --- capture flows ---------------------------------------------------
    def _grab(self, on_image):
        """Capture the full screen, then call on_image(pil_image, src_path)."""
        if self._busy:
            return
        self._busy = True
        backend = self.cfg["capture_backend"]

        def proceed(path):
            try:
                img = imaging.load(path)
                on_image(img, path)
            finally:
                self._busy = False

        # extension path is synchronous & fast; portal path is async.
        try:
            if backend in ("auto", "extension") and capture.extension_available():
                path = capture.capture_fullscreen_extension()
                proceed(path)
                return
        except Exception:
            pass

        def done(path, error):
            if error or not path:
                self._busy = False
                self.notify_error(f"Capture failed: {error or 'no image'}")
                return
            proceed(path)

        capture.capture_fullscreen_portal_async(done)

    def capture_region(self, timer: int = 0):
        if timer and timer > 0:
            self.notify(f"Capturing in {timer}s…")
            GLib.timeout_add_seconds(timer, lambda: (self.capture_region(0), False)[1])
            return

        def on_image(img, src_path):
            def on_done(rect):
                capture.cleanup_capture(src_path)
                if rect is None or rect.is_empty(3):
                    return
                self._last_rect = rect
                self.finish_capture(imaging.crop(img, rect), rect)
            sel = RegionSelector(self, src_path, self.cfg, on_done)
            sel.present()
        self._grab(on_image)

    def capture_fullscreen(self):
        def on_image(img, src_path):
            rect = Rect(0, 0, img.width, img.height)
            self.finish_capture(img, rect)
            capture.cleanup_capture(src_path)
        self._grab(on_image)

    def capture_window(self):
        # True per-window capture comes from the Shell extension; otherwise we
        # fall back to region select (the user can spacebar-snap there later).
        try:
            if capture.extension_available():
                from .windows import capture_active_window
                img, rect = capture_active_window()
                if img is not None:
                    self.finish_capture(img, rect)
                    return
        except Exception:
            pass
        self.capture_region()

    def capture_ocr(self):
        def on_image(img, src_path):
            def on_done(rect):
                capture.cleanup_capture(src_path)
                if rect is None or rect.is_empty(3):
                    return
                cropped = imaging.crop(img, rect)
                try:
                    from .ocr import extract_text
                    text = extract_text(cropped, lang=self.cfg["ocr_lang"])
                    if text.strip():
                        clipboard.copy_text(text)
                        self.notify("Text copied to clipboard")
                    else:
                        self.notify("No text found")
                except Exception as exc:
                    self.notify_error(f"OCR unavailable: {exc}")
            sel = RegionSelector(self, src_path, self.cfg, on_done)
            sel.present()
        self._grab(on_image)

    def capture_previous(self):
        if not self._last_rect:
            self.capture_region()
            return
        rect = self._last_rect

        def on_image(img, src_path):
            r = rect.clamp(Rect(0, 0, img.width, img.height))
            self.finish_capture(imaging.crop(img, r), r)
            capture.cleanup_capture(src_path)
        self._grab(on_image)

    # --- post-capture ----------------------------------------------------
    def finish_capture(self, image, rect: Rect):
        result = CaptureResult(image=image, rect=rect, timestamp=time.time())
        if self.cfg["copy_to_clipboard"]:
            try:
                clipboard.copy_image(result.png)
            except Exception as exc:
                self.notify_error(f"Clipboard copy failed: {exc}")
        if self.cfg["save_to_disk"]:
            result.saved_path = self._save_to_disk(image)
        if self.cfg["history_enabled"]:
            self._add_history(result)
        if self.cfg["show_hud"]:
            self._show_hud(result)
        else:
            self.notify("Screenshot copied")

    def _save_to_disk(self, image) -> Path | None:
        try:
            name = (self.cfg["filename_template"]
                    .replace("{date}", datetime.now().strftime("%Y-%m-%d"))
                    .replace("{time}", datetime.now().strftime("%H-%M-%S")))
            ext = "jpg" if self.cfg["image_format"] in ("jpg", "jpeg") else "png"
            path = Path(self.cfg["save_dir"]) / f"{name}.{ext}"
            return imaging.save(image, path, self.cfg["image_format"])
        except Exception as exc:
            self.notify_error(f"Save failed: {exc}")
            return None

    def _add_history(self, result: CaptureResult):
        try:
            from .history import add_entry
            add_entry(result, HISTORY_DIR, self.cfg["history_max_items"])
        except Exception as exc:
            print(f"[clipshot] history skipped: {exc}", file=sys.stderr)

    def _show_hud(self, result: CaptureResult):
        try:
            from .hud import HudWindow
            HudWindow(self, result).present()
        except Exception as exc:
            print(f"[clipshot] HUD unavailable: {exc}", file=sys.stderr)
            self.notify("Screenshot copied")

    # --- secondary windows (feature modules) -----------------------------
    def open_annotation(self, result: CaptureResult):
        try:
            from .annotate.editor import AnnotationEditor
            AnnotationEditor(self, result).present()
        except Exception as exc:
            self.notify_error(f"Annotation unavailable: {exc}")

    def pin_to_screen(self, result: CaptureResult):
        try:
            from .pin import PinWindow
            PinWindow(self, result).present()
        except Exception as exc:
            self.notify_error(f"Pin unavailable: {exc}")

    def open_settings(self):
        try:
            from .settings_ui import SettingsWindow
            SettingsWindow(self).present()
        except Exception as exc:
            self.notify_error(f"Settings unavailable: {exc}")

    def open_history(self):
        try:
            from .history import HistoryWindow
            HistoryWindow(self).present()
        except Exception as exc:
            self.notify_error(f"History unavailable: {exc}")

    # --- notifications / about ------------------------------------------
    def notify(self, message: str):
        n = Gio.Notification.new(APP_NAME)
        n.set_body(message)
        self.send_notification(None, n)

    def notify_error(self, message: str):
        print(f"[clipshot] {message}", file=sys.stderr)
        n = Gio.Notification.new(f"{APP_NAME} — problem")
        n.set_body(message)
        self.send_notification(None, n)

    def show_about(self):
        about = Adw.AboutWindow(
            application_name=APP_NAME, application_icon="camera-photo-symbolic",
            version=__version__, developer_name="Florin Hrib",
            license_type=Gtk.License.MIT_X11,
            website="https://github.com/florinhrib/clipshot",
            comments="CleanShot-class screenshots for Linux / Wayland.",
        )
        about.present()

    def quit_app(self):
        self.release()
        self.quit()


def main(argv=None):
    Adw.init()
    app = ClipShotApp()
    return app.run(argv if argv is not None else sys.argv)
