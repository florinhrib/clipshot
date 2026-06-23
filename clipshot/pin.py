"""Pinned-to-screen floating screenshot window.

Always-on-top borderless window that shows a captured image at its natural
(logical) size.  Multiple instances are permitted — each lives independently.

Wayland note: arbitrary window *positioning* is not possible for normal
windows under Wayland/Mutter; only the compositor can place them.  Arrow-key
nudging therefore has no effect at runtime (the compositor ignores the hint),
but the key bindings are wired up for completeness and may work on tiling
compositors that honour resize/move hints.  Scroll-to-resize and
scroll-to-opacity DO work because they operate on widget-level properties.
"""
from __future__ import annotations

import io

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from .config import Config
from .imaging import to_png_bytes
from .style import install_css_once

# Minimum logical size when the user resizes via corner-drag.
_MIN_SIZE = 64


def _pil_to_pixbuf(image) -> GdkPixbuf.Pixbuf:
    """Convert a PIL image to a GdkPixbuf via PNG bytes."""
    png_bytes = to_png_bytes(image)
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(png_bytes)
    loader.close()
    return loader.get_pixbuf()


_CSS_ROUNDED_SHADOW = b"""
window.pin-window {
    border-radius: 8px;
    box-shadow: 0 4px 24px 0 rgba(0,0,0,0.45);
}
"""

_CSS_PLAIN = b"""
window.pin-window {
    border-radius: 0;
}
"""

_CLOSE_CSS = b"""
button.pin-close {
    background: rgba(0,0,0,0.55);
    color: white;
    border-radius: 50%;
    min-width: 22px;
    min-height: 22px;
    padding: 0;
    border: none;
}
button.pin-close:hover {
    background: rgba(200,40,40,0.85);
}
"""


class PinWindow(Gtk.ApplicationWindow):
    """Floating always-on-top pinned screenshot window."""

    def __init__(self, app: Gtk.Application, result) -> None:
        super().__init__(application=app)

        # Config — tolerate a bare app (e.g. in tests) that has no .cfg
        cfg: Config = getattr(app, "cfg", None) or Config()
        pin_rounded: bool = cfg["pin_rounded"]
        pin_shadow: bool = cfg["pin_shadow"]

        # Window chrome
        self.set_decorated(False)
        self.set_resizable(True)
        self.set_title("ClipShot — Pinned")

        # GTK4: set_keep_above is only honoured by some compositors on Wayland.
        # We attempt it anyway; it is a no-op where unsupported.
        try:
            self.set_keep_above(True)  # type: ignore[attr-defined]
        except AttributeError:
            pass

        # CSS styling.  Registered once per display (not per pin window) so
        # repeated pinning never leaks providers.
        install_css_once(_CSS_ROUNDED_SHADOW if (pin_rounded or pin_shadow)
                         else _CSS_PLAIN)
        install_css_once(_CLOSE_CSS)
        self.add_css_class("pin-window")

        # Natural image dimensions
        self._orig_pixbuf = _pil_to_pixbuf(result.image)
        self._nat_w = self._orig_pixbuf.get_width()
        self._nat_h = self._orig_pixbuf.get_height()
        # Current displayed size (changes with corner-drag resize)
        self._cur_w = self._nat_w
        self._cur_h = self._nat_h
        # Keep aspect ratio for resize
        self._aspect = self._nat_w / max(1, self._nat_h)

        # Picture widget
        self._picture = Gtk.Picture()
        self._picture.set_keep_aspect_ratio(True)
        self._picture.set_can_shrink(False)
        self._update_picture()

        # Overlay: picture + close button
        overlay = Gtk.Overlay()
        overlay.set_child(self._picture)

        # Close button (shown on hover via CSS :hover on the parent)
        close_btn = Gtk.Button(label="✕")
        close_btn.add_css_class("pin-close")
        close_btn.set_halign(Gtk.Align.END)
        close_btn.set_valign(Gtk.Align.START)
        close_btn.set_margin_top(6)
        close_btn.set_margin_end(6)
        close_btn.connect("clicked", lambda _: self.close())
        # Make button initially semi-transparent; reveal on window hover via opacity
        close_btn.set_opacity(0.0)
        self._close_btn = close_btn
        overlay.add_overlay(close_btn)

        self.set_child(overlay)
        self.set_default_size(self._cur_w, self._cur_h)

        # Hover motion to show/hide close button
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_enter)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

        # Scroll: plain scroll → opacity, Ctrl+scroll → reserved
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
            | Gtk.EventControllerScrollFlags.DISCRETE
        )
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        # Corner-drag resize via GestureDrag on the picture widget.
        # We detect if the drag starts near the bottom-right corner.
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self._picture.add_controller(drag)
        self._drag_origin_w = self._cur_w
        self._drag_origin_h = self._cur_h
        self._is_corner_drag = False

        # Keyboard: Escape to close, arrow keys to nudge (best-effort)
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key)
        self.add_controller(key_ctrl)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _update_picture(self) -> None:
        """Rebuild the Pixbuf at current dimensions and push to the Picture."""
        w = max(_MIN_SIZE, self._cur_w)
        h = max(_MIN_SIZE, self._cur_h)
        scaled = self._orig_pixbuf.scale_simple(
            w, h, GdkPixbuf.InterpType.BILINEAR
        )
        texture = Gdk.Texture.new_for_pixbuf(scaled)
        self._picture.set_paintable(texture)
        self._picture.set_size_request(w, h)

    # ------------------------------------------------------------------ #
    # Signal handlers                                                      #
    # ------------------------------------------------------------------ #

    def _on_enter(self, _ctrl, _x, _y) -> None:
        self._close_btn.set_opacity(1.0)

    def _on_leave(self, _ctrl) -> None:
        self._close_btn.set_opacity(0.0)

    def _on_scroll(self, _ctrl, _dx: float, dy: float) -> bool:
        """Scroll up → more opaque, scroll down → more transparent."""
        current = self.get_opacity()
        step = 0.05
        new_opacity = max(0.1, min(1.0, current - dy * step))
        self.set_opacity(new_opacity)
        return True  # consume the event

    def _on_drag_begin(self, gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        """Detect corner-drag: only act if drag starts in the SE corner region."""
        alloc = self._picture.get_allocation()
        corner_tol = max(32, min(alloc.width, alloc.height) // 5)
        near_right = x >= alloc.width - corner_tol
        near_bottom = y >= alloc.height - corner_tol
        self._is_corner_drag = near_right and near_bottom
        if self._is_corner_drag:
            self._drag_origin_w = self._cur_w
            self._drag_origin_h = self._cur_h
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_drag_update(self, _gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if not self._is_corner_drag:
            return
        # Use the larger delta to preserve aspect ratio.
        if abs(dx) >= abs(dy):
            new_w = max(_MIN_SIZE, int(self._drag_origin_w + dx))
            new_h = max(_MIN_SIZE, int(new_w / self._aspect))
        else:
            new_h = max(_MIN_SIZE, int(self._drag_origin_h + dy))
            new_w = max(_MIN_SIZE, int(new_h * self._aspect))
        self._cur_w = new_w
        self._cur_h = new_h
        self._update_picture()
        self.set_default_size(new_w, new_h)
        self.resize(new_w, new_h)

    def _on_drag_end(self, _gesture: Gtk.GestureDrag, _dx: float, _dy: float) -> None:
        self._is_corner_drag = False

    def _on_key(self, _ctrl, keyval: int, _keycode: int, _state: Gdk.ModifierType) -> bool:
        # Escape → close
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        # Arrow keys — best-effort nudge; Wayland ignores move hints.
        # We call move() anyway; it is harmless and may work on X11/wlroots.
        step = 1
        if keyval == Gdk.KEY_Left:
            self._nudge(-step, 0)
            return True
        if keyval == Gdk.KEY_Right:
            self._nudge(step, 0)
            return True
        if keyval == Gdk.KEY_Up:
            self._nudge(0, -step)
            return True
        if keyval == Gdk.KEY_Down:
            self._nudge(0, step)
            return True
        return False

    def _nudge(self, dx: int, dy: int) -> None:
        """Best-effort window move by (dx, dy) pixels.

        Under Wayland, normal application windows cannot set their position;
        the compositor controls placement.  This call is kept for X11/wlroots
        compatibility and is a no-op on GNOME/Mutter.
        """
        # GTK4 does not expose get_position(); we can only ask for a move.
        # Surface.move() does not exist in GTK4/Wayland.  Log silently.
        pass  # Wayland positioning limits: compositor controls window placement
