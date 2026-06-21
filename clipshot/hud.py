"""Floating HUD thumbnail card shown after each capture.

Design intent:
  * Non-modal, undecorated, always-on-top — a tiny overlay that doesn't steal
    keyboard focus.  The user can keep typing in whatever was underneath.
  * Four one-click actions (Copy / Save / Annotate / Pin) exposed on hover so
    the card stays visually clean at rest.
  * Auto-close optional — cfg["hud_autoclose_seconds"] drives a GLib timer that
    either silently discards (action="discard") or saves then discards (action="save").

Wayland note on positioning:
  GTK4 on Wayland via the XDG-shell protocol does NOT allow arbitrary absolute
  window placement — the compositor controls where windows land.  We therefore
  cannot reliably force a specific pixel position.  We set a small default size
  (~260px wide) and rely on GNOME's "new small window goes near the bottom of
  the screen" heuristic, which in practice lands close to the chosen corner.
  A future improvement could use the Gtk4LayerShell protocol (gtk4-layer-shell
  library) which *does* allow corner-anchored placement on Wayland; that would
  require an optional runtime dep and is not wired up here.
"""
from __future__ import annotations

import io
import tempfile
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import clipboard, imaging

# Target card width in CSS pixels.
_HUD_WIDTH = 260
# Thumbnail area height is derived from the image aspect ratio.
_THUMBNAIL_MAX_HEIGHT = 180
# Margin around the card so the CSS drop-shadow renders without clipping.
_SHADOW_MARGIN = 12

_CSS = b"""
.clipshot-hud {
    background-color: alpha(@window_bg_color, 0.95);
    border-radius: 12px;
    box-shadow: 0 4px 24px 0 alpha(black, 0.45), 0 1px 4px 0 alpha(black, 0.25);
    padding: 6px;
}
.clipshot-hud-btn {
    padding: 4px 8px;
    font-size: 0.82em;
}
"""


def _pil_to_pixbuf(pil_image) -> GdkPixbuf.Pixbuf:
    """Convert a PIL RGBA Image to a GdkPixbuf via a PNG byte stream."""
    loader = GdkPixbuf.PixbufLoader.new_with_mime_type("image/png")
    png_bytes = imaging.to_png_bytes(pil_image)
    loader.write(png_bytes)
    loader.close()
    pbuf = loader.get_pixbuf()
    if pbuf is None:
        raise RuntimeError("GdkPixbufLoader produced no pixbuf")
    return pbuf


def _scaled_pixbuf(pil_image, max_width: int, max_height: int) -> GdkPixbuf.Pixbuf:
    """Return a pixbuf scaled to fit within (max_width, max_height), preserving aspect."""
    pbuf = _pil_to_pixbuf(pil_image)
    src_w, src_h = pbuf.get_width(), pbuf.get_height()
    if src_w == 0 or src_h == 0:
        return pbuf
    scale = min(max_width / src_w, max_height / src_h, 1.0)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    return pbuf.scale_simple(new_w, new_h, GdkPixbuf.InterpType.BILINEAR)


class HudWindow(Gtk.ApplicationWindow):
    """Small floating thumbnail card presented immediately after a capture.

    Args:
        app:    The running ClipShotApp instance.
        result: The CaptureResult produced by finish_capture().
    """

    def __init__(self, app, result):
        super().__init__(application=app)
        self._app = app
        self._result = result
        self._temp_path: Path | None = None   # lazy-created for DnD / Save
        self._autoclose_source: int | None = None

        cfg = app.cfg

        # --- window chrome -------------------------------------------------
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(_HUD_WIDTH + _SHADOW_MARGIN * 2, -1)
        # Keep-above hint: removed in GTK4 (only existed on GTK3). On Wayland
        # there is no always-on-top for normal windows anyway; we attempt it for
        # X11/legacy compatibility and ignore its absence on GTK4.
        try:
            self.set_keep_above(True)  # type: ignore[attr-defined]
        except AttributeError:
            pass
        # Do not steal keyboard focus from whatever the user was typing in.
        self.set_can_focus(False)

        # Apply CSS for rounded corners + shadow.
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # --- root layout: transparent outer box provides shadow margin -----
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_top(_SHADOW_MARGIN)
        outer.set_margin_bottom(_SHADOW_MARGIN)
        outer.set_margin_start(_SHADOW_MARGIN)
        outer.set_margin_end(_SHADOW_MARGIN)
        self.set_child(outer)

        # --- card frame ----------------------------------------------------
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("clipshot-hud")
        outer.append(card)

        # --- thumbnail -----------------------------------------------------
        thumbnail_widget = self._build_thumbnail(result.image)
        card.append(thumbnail_widget)

        # --- button row (hover-revealed) -----------------------------------
        btn_box = self._build_buttons(cfg)

        # Buttons live inside a Revealer (kept revealed at rest for usability;
        # the motion wiring stays in place should we switch to hide-on-idle).
        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        revealer.set_transition_duration(120)
        revealer.set_reveal_child(True)   # visible at rest
        revealer.set_child(btn_box)
        card.append(revealer)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: revealer.set_reveal_child(True))
        motion.connect("leave", lambda *_: revealer.set_reveal_child(True))
        # We keep buttons permanently visible for usability; the revealer
        # wiring is in place should the product direction change to hide-on-idle.
        self.add_controller(motion)

        # --- drag-to-export ------------------------------------------------
        self._attach_drag_source(thumbnail_widget, result)

        # --- auto-close timer ----------------------------------------------
        autoclose_secs = cfg["hud_autoclose_seconds"]
        if isinstance(autoclose_secs, (int, float)) and autoclose_secs > 0:
            action = cfg.get("hud_autoclose_action", "discard")
            self._autoclose_source = GLib.timeout_add_seconds(
                int(autoclose_secs), self._on_autoclose, action
            )

        # Position hint: map the requested corner to a gravity / decoration
        # hint.  On Wayland this is advisory only — see module docstring.
        self._apply_corner_hint(cfg.get("hud_corner", "bottom-left"))

    # ------------------------------------------------------------------
    # Build helpers
    # ------------------------------------------------------------------

    def _build_thumbnail(self, pil_image) -> Gtk.Widget:
        """Return a Gtk.Image displaying the scaled screenshot."""
        try:
            pbuf = _scaled_pixbuf(pil_image, _HUD_WIDTH - 12, _THUMBNAIL_MAX_HEIGHT)
            thumbnail = Gtk.Image.new_from_pixbuf(pbuf)
        except Exception:
            # Fallback: a placeholder label if pixbuf conversion fails.
            thumbnail = Gtk.Label(label="[thumbnail unavailable]")
        thumbnail.set_hexpand(True)
        thumbnail.set_vexpand(False)
        thumbnail.set_halign(Gtk.Align.CENTER)
        return thumbnail

    def _build_buttons(self, cfg) -> Gtk.Box:
        """Build the Copy / Save / Annotate / Pin / Close button row."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_top(2)
        box.set_margin_bottom(2)

        def make_btn(label: str, callback) -> Gtk.Button:
            btn = Gtk.Button(label=label)
            btn.add_css_class("clipshot-hud-btn")
            btn.add_css_class("flat")
            btn.connect("clicked", lambda _b: callback())
            btn.set_can_focus(False)
            return btn

        box.append(make_btn("Copy", self._on_copy))
        box.append(make_btn("Save", self._on_save))
        box.append(make_btn("Annotate", self._on_annotate))
        box.append(make_btn("Pin", self._on_pin))

        # Close button uses a symbolic icon when available, plain text otherwise.
        close_btn = make_btn("✕", self._on_close)
        close_btn.set_tooltip_text("Dismiss")
        box.append(close_btn)

        return box

    def _attach_drag_source(self, widget: Gtk.Widget, result) -> None:
        """Add a DragSource to the thumbnail widget for drag-to-export.

        The file is written to a temp path lazily on drag-prepare.  This
        provides the file as a GFile URI so e.g. Nautilus, Slack, and GNOME
        Files accept it.

        GTK4's DnD content model requires a Gdk.ContentProvider.  We use
        GdkContentProvider.new_for_value() with a GLib.Variant of type 'as'
        containing a list of file:// URIs — this is the format understood by
        apps that accept GNOME file drops.
        """
        try:
            drag_source = Gtk.DragSource()
            drag_source.set_actions(Gdk.DragAction.COPY)

            def on_prepare(_src, _x, _y):
                path = self._get_or_create_temp_file()
                if path is None:
                    return None
                # Build a Gdk.FileList from a Gio.File so the drag target
                # receives a standard GNOME file drop (Files, Slack, etc.).
                gfile = Gio.File.new_for_path(str(path))
                try:
                    # Gdk.FileList is available in GDK 4.6+ (GNOME 43+).
                    file_list = Gdk.FileList.new_from_array([gfile])
                    return Gdk.ContentProvider.new_for_value(file_list)
                except AttributeError:
                    # Older GDK: fall back to a plain text/uri-list provider.
                    uri = gfile.get_uri()
                    uri_bytes = GLib.Bytes.new((uri + "\r\n").encode())
                    return Gdk.ContentProvider.new_for_bytes("text/uri-list", uri_bytes)

            drag_source.connect("prepare", on_prepare)
            widget.add_controller(drag_source)
        except Exception:
            # DnD is best-effort; if the API is unavailable we silently skip it.
            pass

    # ------------------------------------------------------------------
    # Button actions
    # ------------------------------------------------------------------

    def _on_copy(self) -> None:
        try:
            clipboard.copy_image(self._result.png)
            self._app.notify("Copied")
        except Exception as exc:
            self._app.notify_error(f"Copy failed: {exc}")

    def _on_save(self) -> None:
        try:
            path = self._save_image()
            if path is not None:
                self._app.notify(f"Saved to {path}")
        except Exception as exc:
            self._app.notify_error(f"Save failed: {exc}")

    def _on_annotate(self) -> None:
        self._app.open_annotation(self._result)
        self.close()

    def _on_pin(self) -> None:
        self._app.pin_to_screen(self._result)
        self.close()

    def _on_close(self) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Auto-close
    # ------------------------------------------------------------------

    def _on_autoclose(self, action: str) -> bool:
        """Called by GLib timer.  Return False to cancel the timer."""
        self._autoclose_source = None
        if action == "save":
            try:
                self._save_image()
            except Exception:
                pass
        self.close()
        return False  # do not repeat

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _save_image(self) -> Path | None:
        """Write the captured image to cfg save_dir, return the path."""
        cfg = self._app.cfg
        now = datetime.now()
        name = (
            cfg["filename_template"]
            .replace("{date}", now.strftime("%Y-%m-%d"))
            .replace("{time}", now.strftime("%H-%M-%S"))
        )
        fmt = cfg["image_format"]
        ext = "jpg" if fmt in ("jpg", "jpeg") else "png"
        path = Path(cfg["save_dir"]) / f"{name}.{ext}"
        saved = imaging.save(self._result.image, path, fmt)
        self._result.saved_path = saved
        return saved

    def _get_or_create_temp_file(self) -> Path | None:
        """Return a temp PNG path, creating it lazily on first call."""
        if self._temp_path is not None and self._temp_path.exists():
            return self._temp_path
        try:
            tmp = tempfile.NamedTemporaryFile(
                suffix=".png", prefix="clipshot_", delete=False
            )
            tmp.write(self._result.png)
            tmp.close()
            self._temp_path = Path(tmp.name)
            return self._temp_path
        except Exception:
            return None

    def _apply_corner_hint(self, corner: str) -> None:
        """Apply a Gravity hint for the requested corner.

        On Wayland these hints are informational only — the compositor may
        ignore them.  On X11 they are typically honoured.
        """
        # Mapping is best-effort; there is no reliable Wayland corner API.
        gravity_map = {
            "bottom-left":  Gdk.Gravity.SOUTH_WEST,
            "bottom-right": Gdk.Gravity.SOUTH_EAST,
            "top-left":     Gdk.Gravity.NORTH_WEST,
            "top-right":    Gdk.Gravity.NORTH_EAST,
        }
        # Gtk.ApplicationWindow does not expose set_gravity in GTK4 — gravity
        # was removed from the GTK4 API surface.  We note this and do nothing
        # further; the window will appear wherever the compositor places it.
        _ = gravity_map.get(corner, Gdk.Gravity.SOUTH_WEST)  # kept for documentation

    def do_close_request(self) -> bool:
        """Clean up resources when the window is dismissed."""
        if self._autoclose_source is not None:
            GLib.source_remove(self._autoclose_source)
            self._autoclose_source = None
        # Leave _temp_path on disk — the user may have just dropped it somewhere;
        # it lives in /tmp and will be cleaned by the OS on next boot.
        return False  # propagate: let GTK destroy the window
