"""Annotation editor — a real GTK4 window for marking up a capture.

This is the GUI layer: a titled `Gtk.ApplicationWindow` with a `Gtk.DrawingArea`
that renders the captured PIL image with cairo annotations on top, a bottom
toolbar, single-key tool shortcuts, colour + stroke controls and undo/redo.

The *flatten* path (image + annotations -> a final PIL image) is factored into
module-level helpers (`render_to_surface`, `surface_to_pil`, `flatten`) that take
plain PIL images and a list of `tools.Annotation` objects, so the export pipeline
is unit-testable without ever constructing the window or touching `gi`.
"""
from __future__ import annotations

from pathlib import Path

import cairo
from PIL import Image

from .. import clipboard, imaging
from ..geometry import Rect
from . import tools

# --------------------------------------------------------------------------- #
# Headless flatten helpers (NO gi — importable + testable without a display)
# --------------------------------------------------------------------------- #
def _pil_to_surface(img: Image.Image) -> cairo.ImageSurface:
    """RGBA PIL image -> premultiplied BGRA cairo ARGB32 surface."""
    img = img.convert("RGBA")
    w, h = img.size
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    stride = surface.get_stride()
    buf = surface.get_data()
    src = img.tobytes("raw", "RGBA")
    for y in range(h):
        row = y * w * 4
        out = y * stride
        for x in range(w):
            i = row + x * 4
            r, g, b, a = src[i], src[i + 1], src[i + 2], src[i + 3]
            # cairo wants premultiplied, native-endian (little) => B,G,R,A bytes
            o = out + x * 4
            buf[o] = (b * a) // 255
            buf[o + 1] = (g * a) // 255
            buf[o + 2] = (r * a) // 255
            buf[o + 3] = a
    surface.mark_dirty()
    return surface


def surface_to_pil(surface: cairo.ImageSurface) -> Image.Image:
    """Cairo ARGB32 surface -> RGBA PIL image (un-premultiply + BGRA->RGBA)."""
    w = surface.get_width()
    h = surface.get_height()
    stride = surface.get_stride()
    surface.flush()
    data = bytes(surface.get_data())
    out = bytearray(w * h * 4)
    for y in range(h):
        in_row = y * stride
        out_row = y * w * 4
        for x in range(w):
            i = in_row + x * 4
            b, g, r, a = data[i], data[i + 1], data[i + 2], data[i + 3]
            if a:
                r = min(255, (r * 255 + a // 2) // a)
                g = min(255, (g * 255 + a // 2) // a)
                b = min(255, (b * 255 + a // 2) // a)
            o = out_row + x * 4
            out[o] = r
            out[o + 1] = g
            out[o + 2] = b
            out[o + 3] = a
    return Image.frombytes("RGBA", (w, h), bytes(out))


def render_to_surface(
    base_img: Image.Image, objects: list[tools.Annotation]
) -> cairo.ImageSurface:
    """Render base image + vector annotations onto a fresh ARGB32 surface.

    Region effects (blur/pixelate) are baked into a *copy* of the base image
    first; the remaining (vector) annotations are painted with cairo on top.
    """
    work = base_img.convert("RGBA").copy()
    vector: list[tools.Annotation] = []
    for obj in objects:
        baked = obj.apply_to_image(work)  # noqa: F841 - mutates in place
        if not isinstance(obj, tools._RegionEffect):
            vector.append(obj)

    surface = _pil_to_surface(work)
    cr = cairo.Context(surface)
    for obj in vector:
        obj.draw(cr, scale=1.0)
    surface.flush()
    return surface


def flatten(base_img: Image.Image, objects: list[tools.Annotation]) -> Image.Image:
    """Full export: base image + all annotations -> a new RGBA PIL image."""
    surface = render_to_surface(base_img, objects)
    return surface_to_pil(surface)


# --------------------------------------------------------------------------- #
# GTK editor window
# --------------------------------------------------------------------------- #
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

_TOOLBAR = [
    ("V", "select", "Select / Move"),
    ("A", "arrow", "Arrow"),
    ("L", "line", "Line"),
    ("R", "rect", "Rectangle"),
    ("E", "ellipse", "Ellipse"),
    ("T", "text", "Text"),
    ("H", "highlight", "Highlighter"),
    ("C", "counter", "Counter"),
    ("B", "blur", "Blur"),
    ("P", "pixelate", "Pixelate"),
    ("K", "crop", "Crop"),
]


class AnnotationEditor(Gtk.ApplicationWindow):
    def __init__(self, app, result):
        super().__init__(application=app, title="ClipShot — Annotate")
        self._app = app
        self._result = result
        # the working base image; crop replaces it
        self._image: Image.Image = result.image.convert("RGBA").copy()

        self._objects: list[tools.Annotation] = []
        self._history = tools.History()
        self._history.push(self._objects)

        self._tool = "select"
        self._color: tools.RGBA = (1.0, 0.2, 0.2, 1.0)
        self._width = 4.0
        self._counter_next = 1

        # in-flight drag state
        self._drag_obj: tools.Annotation | None = None
        self._drag_new = False
        self._sel: tools.Annotation | None = None
        self._sel_handle: str | None = None
        self._crop_start: tuple[float, float] | None = None
        self._crop_rect: Rect | None = None

        self.set_default_size(
            min(self._image.width + 40, 1400),
            min(self._image.height + 120, 900),
        )

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(root)

        self._area = Gtk.DrawingArea()
        self._area.set_hexpand(True)
        self._area.set_vexpand(True)
        self._area.set_content_width(self._image.width)
        self._area.set_content_height(self._image.height)
        self._area.set_draw_func(self._on_draw)
        root.append(self._area)

        root.append(self._build_toolbar())
        self._wire_input()

    # -- toolbar ----------------------------------------------------------
    def _build_toolbar(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.set_margin_top(6)
        bar.set_margin_bottom(6)
        bar.set_margin_start(6)
        bar.set_margin_end(6)

        self._tool_buttons: dict[str, Gtk.ToggleButton] = {}
        group: Gtk.ToggleButton | None = None
        for key, name, tip in _TOOLBAR:
            btn = Gtk.ToggleButton(label=key)
            btn.set_tooltip_text(f"{tip} ({key})")
            if group is None:
                group = btn
            else:
                btn.set_group(group)
            btn.connect("toggled", self._on_tool_toggled, name)
            self._tool_buttons[name] = btn
            bar.append(btn)
        self._tool_buttons["select"].set_active(True)

        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # colour picker
        try:
            self._color_btn = Gtk.ColorDialogButton.new(Gtk.ColorDialog())
            rgba = Gdk.RGBA()
            rgba.red, rgba.green, rgba.blue, rgba.alpha = self._color
            self._color_btn.set_rgba(rgba)
            self._color_btn.connect("notify::rgba", self._on_color)
            bar.append(self._color_btn)
        except Exception:
            self._color_btn = None

        # stroke width
        adj = Gtk.Adjustment(value=self._width, lower=1, upper=40, step_increment=1)
        self._width_spin = Gtk.SpinButton(adjustment=adj)
        self._width_spin.set_tooltip_text("Stroke width")
        self._width_spin.connect("value-changed", self._on_width)
        bar.append(self._width_spin)

        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        undo = Gtk.Button(label="Undo")
        undo.connect("clicked", lambda *_: self._undo())
        bar.append(undo)
        redo = Gtk.Button(label="Redo")
        redo.connect("clicked", lambda *_: self._redo())
        bar.append(redo)

        spacer = Gtk.Box(hexpand=True)
        bar.append(spacer)

        copy = Gtk.Button(label="Copy")
        copy.connect("clicked", lambda *_: self._do_copy())
        bar.append(copy)
        save = Gtk.Button(label="Save")
        save.connect("clicked", lambda *_: self._do_save())
        bar.append(save)
        done = Gtk.Button(label="Done")
        done.connect("clicked", lambda *_: self.close())
        bar.append(done)
        return bar

    # -- input wiring -----------------------------------------------------
    def _wire_input(self) -> None:
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self._area.add_controller(drag)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # -- tool/colour/width handlers --------------------------------------
    def _on_tool_toggled(self, btn: Gtk.ToggleButton, name: str) -> None:
        if btn.get_active():
            self._tool = name
            if name != "select":
                self._sel = None
            self._area.queue_draw()

    def _set_tool(self, name: str) -> None:
        btn = self._tool_buttons.get(name)
        if btn:
            btn.set_active(True)

    def _on_color(self, btn, _pspec) -> None:
        c = btn.get_rgba()
        self._color = (c.red, c.green, c.blue, c.alpha)
        if self._sel is not None:
            self._sel.color = self._color
            self._commit()

    def _on_width(self, spin: Gtk.SpinButton) -> None:
        self._width = spin.get_value()
        if self._sel is not None:
            self._sel.width = self._width
            self._commit()

    # -- keyboard ---------------------------------------------------------
    def _on_key(self, _ctrl, keyval, _code, state) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        name = Gdk.keyval_name(keyval) or ""
        low = name.lower()

        if ctrl and low == "z" and shift:
            self._redo()
            return True
        if ctrl and low == "z":
            self._undo()
            return True
        if ctrl and shift and low == "c":
            self._do_copy()
            return True
        if name == "Escape":
            self.close()
            return True
        if name == "Delete" and self._sel is not None:
            self._objects.remove(self._sel)
            self._sel = None
            self._commit()
            return True

        # single-key tool shortcuts (no modifiers)
        if not ctrl and len(low) == 1:
            if low == "v":
                self._set_tool("select")
                return True
            if low == "k":
                self._set_tool("crop")
                return True
            if low in tools.SHORTCUTS:
                self._set_tool(tools.SHORTCUTS[low])
                return True
        return False

    # -- drawing ----------------------------------------------------------
    def _on_draw(self, _area, cr, width, height) -> None:
        # scale the image to fit the drawing area while preserving aspect
        iw, ih = self._image.width, self._image.height
        scale = min(width / iw, height / ih) if iw and ih else 1.0
        self._scale = scale

        cr.save()
        cr.scale(scale, scale)
        surface = _pil_to_surface(self._image)
        cr.set_source_surface(surface, 0, 0)
        cr.paint()
        cr.restore()

        cr.save()
        for obj in self._objects:
            obj.draw(cr, scale=scale)
        cr.restore()

        if self._sel is not None:
            self._draw_selection(cr, self._sel, scale)
        if self._crop_rect is not None:
            self._draw_crop(cr, self._crop_rect, scale)

    def _draw_selection(self, cr, obj: tools.Annotation, scale: float) -> None:
        b = obj.bounds()
        cr.save()
        cr.set_source_rgba(0.2, 0.6, 1.0, 0.9)
        cr.set_line_width(1.0)
        cr.set_dash([4, 3])
        cr.rectangle(b.x * scale, b.y * scale, b.w * scale, b.h * scale)
        cr.stroke()
        cr.restore()

    def _draw_crop(self, cr, rect: Rect, scale: float) -> None:
        cr.save()
        cr.set_source_rgba(0.1, 0.5, 1.0, 0.9)
        cr.set_line_width(1.5)
        cr.set_dash([6, 4])
        cr.rectangle(rect.x * scale, rect.y * scale, rect.w * scale, rect.h * scale)
        cr.stroke()
        cr.restore()

    # -- coordinate mapping (widget -> image px) -------------------------
    def _img_xy(self, wx: float, wy: float) -> tuple[float, float]:
        s = getattr(self, "_scale", 1.0) or 1.0
        return wx / s, wy / s

    # -- gesture handlers -------------------------------------------------
    def _on_drag_begin(self, gesture, start_x, start_y) -> None:
        x, y = self._img_xy(start_x, start_y)
        self._drag_obj = None
        self._drag_new = False
        self._sel_handle = None
        self._crop_start = None
        self._crop_rect = None

        if self._tool == "crop":
            self._crop_start = (x, y)
            return

        if self._tool == "select":
            # hit-test existing objects topmost-first
            for obj in reversed(self._objects):
                h = obj.handle_at(x, y)
                if h is not None:
                    self._sel = obj
                    self._sel_handle = h
                    self._area.queue_draw()
                    return
            self._sel = None
            self._area.queue_draw()
            return

        if self._tool == "text":
            self._place_text(x, y)
            return

        if self._tool == "counter":
            obj = tools.CounterTool(
                x0=x, y0=y, x1=x, y1=y,
                color=self._color, number=self._counter_next,
            )
            self._counter_next += 1
            self._objects.append(obj)
            self._commit()
            return

        # create a new draggable vector object
        obj = tools.make(
            self._tool, x0=x, y0=y, x1=x, y1=y,
            color=self._color, width=self._width,
        )
        self._objects.append(obj)
        self._drag_obj = obj
        self._drag_new = True

    def _on_drag_update(self, gesture, off_x, off_y) -> None:
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return
        x, y = self._img_xy(sx + off_x, sy + off_y)
        dxy = self._img_xy(off_x, off_y)

        if self._tool == "crop" and self._crop_start is not None:
            x0, y0 = self._crop_start
            self._crop_rect = Rect.from_points(x0, y0, x, y)
            self._area.queue_draw()
            return

        if self._tool == "select" and self._sel is not None:
            sxp, syp = self._img_xy(sx, sy)
            dx, dy = x - getattr(self, "_last_x", sxp), y - getattr(self, "_last_y", syp)
            if self._sel_handle == "inside":
                self._sel.move(dx, dy)
            elif self._sel_handle:
                self._sel.resize(self._sel_handle, dx, dy)
            self._last_x, self._last_y = x, y
            self._area.queue_draw()
            return

        if self._drag_obj is not None:
            self._drag_obj.x1 = x
            self._drag_obj.y1 = y
            self._area.queue_draw()

    def _on_drag_end(self, gesture, off_x, off_y) -> None:
        self._last_x = None
        self._last_y = None

        if self._tool == "crop" and self._crop_rect is not None:
            self._apply_crop(self._crop_rect)
            self._crop_rect = None
            self._crop_start = None
            return

        if self._drag_new and self._drag_obj is not None:
            # discard degenerate scribbles
            b = self._drag_obj.bounds()
            if b.is_empty(2) and not isinstance(self._drag_obj, tools.CounterTool):
                if self._drag_obj in self._objects:
                    self._objects.remove(self._drag_obj)
            self._commit()
        elif self._sel is not None:
            self._commit()
        self._drag_obj = None
        self._drag_new = False

    # -- text placement ---------------------------------------------------
    def _place_text(self, x: float, y: float) -> None:
        popover = Gtk.Popover()
        popover.set_parent(self._area)
        rect = Gdk.Rectangle()
        s = getattr(self, "_scale", 1.0)
        rect.x, rect.y, rect.width, rect.height = int(x * s), int(y * s), 1, 1
        popover.set_pointing_to(rect)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Text…")
        popover.set_child(entry)

        def commit_text(*_a):
            txt = entry.get_text()
            popover.popdown()
            if txt:
                obj = tools.TextTool(
                    x0=x, y0=y, x1=x, y1=y,
                    color=self._color, text=txt,
                    font_size=max(12.0, self._width * 5),
                )
                self._objects.append(obj)
                self._commit()

        entry.connect("activate", commit_text)
        popover.popup()
        entry.grab_focus()

    # -- crop -------------------------------------------------------------
    def _apply_crop(self, rect: Rect) -> None:
        r = rect.clamp(Rect(0, 0, self._image.width, self._image.height))
        if r.is_empty(4):
            return
        self._image = self._image.crop((r.x, r.y, r.x1, r.y1))
        # shift annotations into the new origin
        for obj in self._objects:
            obj.move(-r.x, -r.y)
        self._area.set_content_width(self._image.width)
        self._area.set_content_height(self._image.height)
        self._commit()

    # -- history ----------------------------------------------------------
    def _commit(self) -> None:
        self._history.push(self._objects)
        self._area.queue_draw()

    def _undo(self) -> None:
        snap = self._history.undo(self._objects)
        if snap is not None:
            self._objects = snap
            self._sel = None
            self._area.queue_draw()

    def _redo(self) -> None:
        snap = self._history.redo()
        if snap is not None:
            self._objects = snap
            self._sel = None
            self._area.queue_draw()

    # -- export -----------------------------------------------------------
    def _flatten(self) -> Image.Image:
        return flatten(self._image, self._objects)

    def _do_copy(self) -> None:
        try:
            out = self._flatten()
            clipboard.copy_image(imaging.to_png_bytes(out))
            self._app.notify("Annotated screenshot copied")
        except Exception as exc:  # pragma: no cover - GUI path
            self._app.notify_error(f"Copy failed: {exc}")

    def _do_save(self) -> None:
        try:
            out = self._flatten()
            cfg = self._app.cfg
            from datetime import datetime

            name = (cfg["filename_template"]
                    .replace("{date}", datetime.now().strftime("%Y-%m-%d"))
                    .replace("{time}", datetime.now().strftime("%H-%M-%S")))
            fmt = cfg["image_format"]
            ext = "jpg" if fmt in ("jpg", "jpeg") else "png"
            path = Path(cfg["save_dir"]) / f"{name}-annotated.{ext}"
            saved = imaging.save(out, path, fmt)
            self._app.notify(f"Saved to {saved}")
        except Exception as exc:  # pragma: no cover - GUI path
            self._app.notify_error(f"Save failed: {exc}")


__all__ = [
    "AnnotationEditor",
    "flatten",
    "render_to_surface",
    "surface_to_pil",
]
