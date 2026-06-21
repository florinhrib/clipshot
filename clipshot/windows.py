"""Per-window capture via the ClipShot GNOME Shell extension D-Bus interface.

The extension owns the well-known session bus name ``uk.florinlab.ClipShot``
and exposes ``CaptureActiveWindow() -> (s path, i x, i y, i w, i h)``.
GTK / GObject imports are kept lazy so this module is safe to import in
headless contexts.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image

    from .geometry import Rect


def capture_active_window() -> "tuple[Image | None, Rect | None]":
    """Capture the currently focused window via the Shell extension.

    Calls ``CaptureActiveWindow`` on the extension D-Bus interface and
    returns ``(PIL.Image, Rect)`` on success, or ``(None, None)`` on any
    failure (extension absent, D-Bus error, file missing, …).
    """
    try:
        import gi

        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib  # noqa: E402

        from . import capture as _capture
        from .geometry import Rect
        from .imaging import load

        if not _capture.extension_available():
            return None, None

        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = conn.call_sync(
            _capture.EXT_BUS,
            _capture.EXT_PATH,
            _capture.EXT_IFACE,
            "CaptureActiveWindow",
            None,
            GLib.VariantType("(siiii)"),
            Gio.DBusCallFlags.NONE,
            5000,
            None,
        )
        path_str, x, y, w, h = reply.unpack()
        img = load(path_str)
        return img, Rect(x, y, w, h)

    except Exception:  # noqa: BLE001  — degrade gracefully on any failure
        return None, None
