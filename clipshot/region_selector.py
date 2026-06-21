"""Fullscreen region selector — the CleanShot-style drag-to-crop overlay.

Renders the *frozen* full-screen still (captured beforehand) and lets the user
rubber-band a selection on it with:
  * full-width/height crosshair lines,
  * a pixel magnifier loupe near the cursor,
  * a live W×H dimensions badge,
  * adjustable selection (drag handles / move inside / arrow-key nudge),
  * Shift to lock aspect while resizing, Escape to cancel, Enter/double-click to confirm.

Because we draw onto a still image inside a normal GTK4 window, this works on
GNOME/Mutter without layer-shell.  Coordinates are mapped widget->image so the
result is correct on HiDPI/fractional scaling.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from .config import Config
from .geometry import Rect, handle_at

CURSOR_BY_HANDLE = {
    "nw": "nw-resize", "ne": "ne-resize", "sw": "sw-resize", "se": "se-resize",
    "n": "n-resize", "s": "s-resize", "e": "e-resize", "w": "w-resize",
    "inside": "move",
}


def _parse_color(hexstr: str) -> tuple[float, float, float]:
    hexstr = hexstr.lstrip("#")
    r = int(hexstr[0:2], 16) / 255
    g = int(hexstr[2:4], 16) / 255
    b = int(hexstr[4:6], 16) / 255
    return r, g, b


class RegionSelector(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, image_path: str | Path,
                 config: Config, on_done: Callable[[Rect | None], None]):
        super().__init__(application=app)
        self.cfg = config
        self.on_done = on_done
        self._finished = False
        self.pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(image_path))
        self.img_w = self.pixbuf.get_width()
        self.img_h = self.pixbuf.get_height()

        # selection state, in IMAGE pixels
        self.sel: Rect | None = None
        self._drag_start: tuple[float, float] | None = None
        self._mode = "draw"           # draw | move | resize
        self._resize_edge = ""
        self._sel_at_grab: Rect | None = None
        self._cursor_img = (0.0, 0.0)  # cursor in image px (for crosshair/magnifier)
        self._hover_handle: str | None = None
        self.accent = _parse_color(self.cfg["selection_color"])

        self.set_decorated(False)
        self.set_resizable(False)
        self.fullscreen()

        self.area = Gtk.DrawingArea()
        self.area.set_hexpand(True)
        self.area.set_vexpand(True)
        self.area.set_draw_func(self._draw)
        self.set_child(self.area)

        self._install_controllers()

    # --- coordinate mapping (widget <-> image) ---------------------------
    def _scale(self) -> float:
        alloc_w = self.area.get_allocated_width() or self.img_w
        return self.img_w / alloc_w if alloc_w else 1.0

    def _to_img(self, wx: float, wy: float) -> tuple[float, float]:
        s = self._scale()
        return wx * s, wy * s

    def _to_widget(self, ix: float, iy: float) -> tuple[float, float]:
        s = self._scale()
        return ix / s, iy / s

    # --- controllers -----------------------------------------------------
    def _install_controllers(self):
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.area.add_controller(drag)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.area.add_controller(motion)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.area.add_controller(click)

    # --- pointer ---------------------------------------------------------
    def _on_motion(self, _ctrl, x, y):
        self._cursor_img = self._to_img(x, y)
        if self.sel and not self._drag_start:
            ix, iy = self._cursor_img
            self._hover_handle = handle_at(self.sel, ix, iy, tol=10 * self._scale())
            name = CURSOR_BY_HANDLE.get(self._hover_handle or "", "crosshair")
            self.set_cursor(Gdk.Cursor.new_from_name(name, None))
        else:
            self.set_cursor(Gdk.Cursor.new_from_name("crosshair", None))
        self.area.queue_draw()

    def _on_drag_begin(self, _g, start_x, start_y):
        ix, iy = self._to_img(start_x, start_y)
        self._drag_start = (ix, iy)
        if self.sel:
            edge = handle_at(self.sel, ix, iy, tol=10 * self._scale())
            if edge == "inside":
                self._mode, self._sel_at_grab = "move", self.sel
                return
            if edge:
                self._mode, self._resize_edge, self._sel_at_grab = "resize", edge, self.sel
                return
        self._mode = "draw"

    def _on_drag_update(self, _g, off_x, off_y):
        if not self._drag_start:
            return
        s = self._scale()
        dix, diy = off_x * s, off_y * s
        sx, sy = self._drag_start
        if self._mode == "draw":
            self.sel = Rect.from_points(sx, sy, sx + dix, sy + diy).clamp(
                Rect(0, 0, self.img_w, self.img_h))
        elif self._mode == "move" and self._sel_at_grab:
            self.sel = self._sel_at_grab.translated(int(dix), int(diy)).clamp(
                Rect(0, 0, self.img_w, self.img_h))
        elif self._mode == "resize" and self._sel_at_grab:
            self.sel = self._sel_at_grab.grown(self._resize_edge, int(dix), int(diy)).clamp(
                Rect(0, 0, self.img_w, self.img_h))
        self.area.queue_draw()

    def _on_drag_end(self, _g, _x, _y):
        self._drag_start = None
        self._mode = "draw"

    def _on_click(self, _g, n_press, x, y):
        if n_press == 2 and self.sel and not self.sel.is_empty(5):
            self._confirm()

    # --- keyboard --------------------------------------------------------
    def _on_key(self, _ctrl, keyval, _code, state):
        name = Gdk.keyval_name(keyval)
        if name in ("Escape",):
            self._cancel()
            return True
        if name in ("Return", "KP_Enter"):
            if self.sel and not self.sel.is_empty(5):
                self._confirm()
            return True
        if self.sel and name in ("Left", "Right", "Up", "Down"):
            step = 10 if (state & Gdk.ModifierType.SHIFT_MASK) else 1
            dx = (-step if name == "Left" else step if name == "Right" else 0)
            dy = (-step if name == "Up" else step if name == "Down" else 0)
            # Shift+arrow resizes the SE corner; plain arrow moves the selection
            if state & Gdk.ModifierType.CONTROL_MASK:
                self.sel = self.sel.grown("se", dx, dy)
            else:
                self.sel = self.sel.translated(dx, dy).clamp(Rect(0, 0, self.img_w, self.img_h))
            self.area.queue_draw()
            return True
        return False

    # --- finish ----------------------------------------------------------
    def _confirm(self):
        if self._finished:
            return
        self._finished = True
        rect = self.sel
        self.close()
        GLib.idle_add(self.on_done, rect)

    def _cancel(self):
        if self._finished:
            return
        self._finished = True
        self.close()
        GLib.idle_add(self.on_done, None)

    # --- drawing ---------------------------------------------------------
    def _draw(self, _area, cr, width, height):
        # background: the captured still scaled to the widget
        scale = width / self.img_w
        cr.save()
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint()
        cr.restore()

        dim = float(self.cfg["dim_opacity"])
        if self.sel and not self.sel.is_empty():
            wx, wy = self._to_widget(self.sel.x, self.sel.y)
            ww = self.sel.w / self._scale()
            wh = self.sel.h / self._scale()
            # dim everything, then punch out the selection (re-paint it bright)
            cr.set_source_rgba(0, 0, 0, dim)
            cr.rectangle(0, 0, width, height)
            cr.rectangle(wx, wy, ww, wh)
            cr.set_fill_rule(1)  # EVEN_ODD -> hole
            cr.fill()
            cr.set_fill_rule(0)
            # selection border
            r, g, b = self.accent
            cr.set_source_rgb(r, g, b)
            cr.set_line_width(1.5)
            cr.rectangle(wx + 0.5, wy + 0.5, ww, wh)
            cr.stroke()
            self._draw_handles(cr, wx, wy, ww, wh)
            if self.cfg["show_dimensions"]:
                self._draw_dimensions(cr, wx, wy, ww, wh, width, height)
        else:
            cr.set_source_rgba(0, 0, 0, dim)
            cr.rectangle(0, 0, width, height)
            cr.fill()

        if self.cfg["show_crosshair"] and not self._drag_start:
            self._draw_crosshair(cr, width, height)
        if self.cfg["show_magnifier"]:
            self._draw_magnifier(cr, width, height)

    def _draw_handles(self, cr, x, y, w, h):
        r, g, b = self.accent
        pts = [(x, y), (x + w / 2, y), (x + w, y), (x + w, y + h / 2),
               (x + w, y + h), (x + w / 2, y + h), (x, y + h), (x, y + h / 2)]
        for px, py in pts:
            cr.set_source_rgb(1, 1, 1)
            cr.arc(px, py, 4.5, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgb(r, g, b)
            cr.arc(px, py, 4.5, 0, 2 * math.pi)
            cr.set_line_width(1.5)
            cr.stroke()

    def _draw_dimensions(self, cr, x, y, w, h, sw, sh):
        label = f"{self.sel.w} × {self.sel.h}"
        cr.select_font_face("monospace", 0, 1)
        cr.set_font_size(13)
        ext = cr.text_extents(label)
        pad = 6
        bw, bh = ext.width + 2 * pad, ext.height + 2 * pad
        bx = x
        by = y - bh - 6
        if by < 4:
            by = y + 6
        bx = max(4, min(bx, sw - bw - 4))
        cr.set_source_rgba(0, 0, 0, 0.8)
        cr.rectangle(bx, by, bw, bh)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(bx + pad, by + pad + ext.height)
        cr.show_text(label)

    def _draw_crosshair(self, cr, width, height):
        wx, wy = self._to_widget(*self._cursor_img)
        cr.set_source_rgba(*self.accent, 0.6)
        cr.set_line_width(1.0)
        cr.move_to(0, wy + 0.5)
        cr.line_to(width, wy + 0.5)
        cr.move_to(wx + 0.5, 0)
        cr.line_to(wx + 0.5, height)
        cr.stroke()

    def _draw_magnifier(self, cr, width, height):
        ix, iy = self._cursor_img
        wx, wy = self._to_widget(ix, iy)
        size = 110          # loupe diameter (widget px)
        zoom = 8            # source pixels shown across the loupe
        src = size / zoom
        # position loupe offset from cursor, flip near edges
        ox, oy = wx + 20, wy + 20
        if ox + size > width:
            ox = wx - size - 20
        if oy + size > height:
            oy = wy - size - 20
        sx = int(ix - src / 2)
        sy = int(iy - src / 2)
        sub_w = int(src)
        sub_h = int(src)
        if sub_w <= 0 or sub_h <= 0:
            return
        sx = max(0, min(sx, self.img_w - sub_w))
        sy = max(0, min(sy, self.img_h - sub_h))
        try:
            sub = self.pixbuf.new_subpixbuf(sx, sy, sub_w, sub_h)
        except Exception:
            return
        if sub is None:
            return
        cr.save()
        cr.rectangle(ox, oy, size, size)
        cr.clip()
        cr.translate(ox, oy)
        cr.scale(size / sub_w, size / sub_h)
        Gdk.cairo_set_source_pixbuf(cr, sub, 0, 0)
        cr.get_source().set_filter(0)  # NEAREST -> crisp pixels
        cr.paint()
        cr.restore()
        # loupe frame + center marker
        cr.set_source_rgb(*self.accent)
        cr.set_line_width(2)
        cr.rectangle(ox, oy, size, size)
        cr.stroke()
        cr.set_source_rgba(*self.accent, 0.9)
        cr.set_line_width(1)
        cr.move_to(ox + size / 2, oy)
        cr.line_to(ox + size / 2, oy + size)
        cr.move_to(ox, oy + size / 2)
        cr.line_to(ox + size, oy + size / 2)
        cr.stroke()
