"""Annotation tool objects — pure(-ish) cairo + PIL, no GTK.

Every annotation is a small dataclass holding its geometry, colour and stroke,
plus a `draw(cr, scale)` method that paints it onto a cairo context and helpers
for hit-testing / moving / resizing so the editor can keep them editable.

Keeping this module free of `gi`/GTK means the whole draw + flatten path is
unit-testable on a headless box (see tests/test_annotate_flatten.py).

Coordinates are stored in *image pixels* (the same space as geometry.Rect).
`draw(cr, scale)` multiplies by `scale` so the editor can render at a zoom level
while flatten renders at scale == 1.0.

Blur/Pixelate are special: they don't paint with cairo at flatten time, instead
they mutate the underlying PIL base image region.  At *editor* draw time they
render a translucent placeholder so the user can see/move them.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass

from ..geometry import Rect, handle_at

RGBA = tuple[float, float, float, float]


# --------------------------------------------------------------------------- #
# Base
# --------------------------------------------------------------------------- #
@dataclass
class Annotation:
    """Common geometry + style.  Concrete tools fill in `draw`."""

    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    color: RGBA = (1.0, 0.2, 0.2, 1.0)
    width: float = 4.0

    #: human label, also the registry key
    kind: str = "annotation"

    # -- geometry helpers --------------------------------------------------
    def bounds(self) -> Rect:
        return Rect.from_points(self.x0, self.y0, self.x1, self.y1)

    def _apply_color(self, cr) -> None:
        cr.set_source_rgba(*self.color)

    # -- editing -----------------------------------------------------------
    def move(self, dx: float, dy: float) -> None:
        self.x0 += dx
        self.y0 += dy
        self.x1 += dx
        self.y1 += dy

    def resize(self, handle: str, dx: float, dy: float) -> None:
        """Drag a named handle (n/s/e/w combos) by dx/dy in image pixels."""
        if "w" in handle:
            self.x0 += dx
        if "e" in handle:
            self.x1 += dx
        if "n" in handle:
            self.y0 += dy
        if "s" in handle:
            self.y1 += dy

    def handle_at(self, px: float, py: float, tol: float = 10.0) -> str | None:
        """Which handle (or 'inside') the point is over, in image pixels.

        Endpoint-style tools (arrow/line) override this since their two points
        are the natural handles rather than a bounding box.
        """
        return handle_at(self.bounds(), px, py, tol)

    def hit(self, px: float, py: float, tol: float = 10.0) -> bool:
        return self.handle_at(px, py, tol) is not None

    # -- flatten hook ------------------------------------------------------
    def apply_to_image(self, base_img) -> None:  # noqa: D401 - hook
        """Mutate the base PIL image (blur/pixelate). No-op for vector tools."""
        return None

    # subclasses implement draw(self, cr, scale)
    def draw(self, cr, scale: float = 1.0) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Endpoint tools (two free points)
# --------------------------------------------------------------------------- #
@dataclass
class _Endpoint(Annotation):
    """Tools defined by a start point and an end point."""

    def handle_at(self, px: float, py: float, tol: float = 10.0) -> str | None:
        if math.hypot(px - self.x0, py - self.y0) <= tol:
            return "start"
        if math.hypot(px - self.x1, py - self.y1) <= tol:
            return "end"
        # near the segment itself -> move
        if _point_near_segment(px, py, self.x0, self.y0, self.x1, self.y1, tol):
            return "inside"
        return None

    def resize(self, handle: str, dx: float, dy: float) -> None:
        if handle == "start":
            self.x0 += dx
            self.y0 += dy
        elif handle == "end":
            self.x1 += dx
            self.y1 += dy
        else:
            super().resize(handle, dx, dy)


@dataclass
class LineTool(_Endpoint):
    kind: str = "line"

    def draw(self, cr, scale: float = 1.0) -> None:
        cr.save()
        self._apply_color(cr)
        cr.set_line_width(self.width * scale)
        cr.set_line_cap(1)  # ROUND
        cr.move_to(self.x0 * scale, self.y0 * scale)
        cr.line_to(self.x1 * scale, self.y1 * scale)
        cr.stroke()
        cr.restore()


@dataclass
class ArrowTool(_Endpoint):
    kind: str = "arrow"

    def draw(self, cr, scale: float = 1.0) -> None:
        cr.save()
        self._apply_color(cr)
        w = self.width * scale
        cr.set_line_width(w)
        cr.set_line_cap(1)
        cr.set_line_join(1)  # ROUND
        x0, y0 = self.x0 * scale, self.y0 * scale
        x1, y1 = self.x1 * scale, self.y1 * scale
        ang = math.atan2(y1 - y0, x1 - x0)
        head = max(12.0, w * 3.0)
        spread = math.radians(28)
        # shorten the shaft so it tucks under the head
        bx = x1 - math.cos(ang) * head * 0.8
        by = y1 - math.sin(ang) * head * 0.8
        cr.move_to(x0, y0)
        cr.line_to(bx, by)
        cr.stroke()
        # filled triangular head
        cr.move_to(x1, y1)
        cr.line_to(
            x1 - math.cos(ang - spread) * head,
            y1 - math.sin(ang - spread) * head,
        )
        cr.line_to(
            x1 - math.cos(ang + spread) * head,
            y1 - math.sin(ang + spread) * head,
        )
        cr.close_path()
        cr.fill()
        cr.restore()


# --------------------------------------------------------------------------- #
# Box tools (bounding rect)
# --------------------------------------------------------------------------- #
@dataclass
class RectTool(Annotation):
    kind: str = "rect"

    def draw(self, cr, scale: float = 1.0) -> None:
        cr.save()
        self._apply_color(cr)
        cr.set_line_width(self.width * scale)
        cr.set_line_join(1)
        b = self.bounds()
        cr.rectangle(b.x * scale, b.y * scale, b.w * scale, b.h * scale)
        cr.stroke()
        cr.restore()


@dataclass
class EllipseTool(Annotation):
    kind: str = "ellipse"

    def draw(self, cr, scale: float = 1.0) -> None:
        b = self.bounds()
        cx = (b.x + b.w / 2.0) * scale
        cy = (b.y + b.h / 2.0) * scale
        rx = max(b.w / 2.0, 0.5) * scale
        ry = max(b.h / 2.0, 0.5) * scale
        cr.save()
        self._apply_color(cr)
        # stroke uniformly: build the unit-circle path under a scale transform,
        # but stroke after restoring so the line width isn't distorted.
        cr.translate(cx, cy)
        cr.scale(rx, ry)
        cr.new_sub_path()
        cr.arc(0, 0, 1.0, 0, 2 * math.pi)
        cr.restore()
        cr.save()
        self._apply_color(cr)
        cr.set_line_width(self.width * scale)
        cr.stroke()
        cr.restore()


@dataclass
class HighlightTool(Annotation):
    """Semi-transparent filled rectangle (a marker swipe)."""

    color: RGBA = (1.0, 0.92, 0.2, 0.4)
    kind: str = "highlight"

    def draw(self, cr, scale: float = 1.0) -> None:
        cr.save()
        # honour alpha from colour but keep it translucent
        r, g, b, a = self.color
        cr.set_source_rgba(r, g, b, min(a, 0.5))
        bb = self.bounds()
        cr.rectangle(bb.x * scale, bb.y * scale, bb.w * scale, bb.h * scale)
        cr.fill()
        cr.restore()


# --------------------------------------------------------------------------- #
# Text
# --------------------------------------------------------------------------- #
@dataclass
class TextTool(Annotation):
    text: str = ""
    font_size: float = 24.0
    kind: str = "text"

    def draw(self, cr, scale: float = 1.0) -> None:
        if not self.text:
            return
        cr.save()
        self._apply_color(cr)
        cr.select_font_face("Sans", 0, 1)  # NORMAL slant, BOLD weight
        cr.set_font_size(self.font_size * scale)
        x = self.x0 * scale
        # cairo text baseline sits at y; offset by the ascent so x0/y0 is top-left
        ascent = cr.font_extents()[0]
        cr.move_to(x, self.y0 * scale + ascent)
        cr.show_text(self.text)
        cr.restore()

    def bounds(self) -> Rect:
        # approximate box from font metrics so it's selectable before draw
        h = self.font_size * 1.3
        w = max(len(self.text), 1) * self.font_size * 0.6
        return Rect(int(self.x0), int(self.y0), int(w), int(h))

    def handle_at(self, px: float, py: float, tol: float = 10.0) -> str | None:
        b = self.bounds()
        if b.x - tol <= px <= b.x1 + tol and b.y - tol <= py <= b.y1 + tol:
            return "inside"
        return None


# --------------------------------------------------------------------------- #
# Counter — auto-incrementing numbered circle
# --------------------------------------------------------------------------- #
@dataclass
class CounterTool(Annotation):
    number: int = 1
    radius: float = 18.0
    color: RGBA = (0.9, 0.15, 0.2, 1.0)
    kind: str = "counter"

    def draw(self, cr, scale: float = 1.0) -> None:
        cr.save()
        cx, cy, r = self.x0 * scale, self.y0 * scale, self.radius * scale
        # filled disc
        self._apply_color(cr)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.fill()
        # number, white, centred
        cr.set_source_rgba(1, 1, 1, 1)
        cr.select_font_face("Sans", 0, 1)
        cr.set_font_size(r * 1.1)
        label = str(self.number)
        ext = cr.text_extents(label)
        cr.move_to(cx - ext[2] / 2 - ext[0], cy + ext[3] / 2)
        cr.show_text(label)
        cr.restore()

    def bounds(self) -> Rect:
        r = self.radius
        return Rect.from_points(self.x0 - r, self.y0 - r, self.x0 + r, self.y0 + r)

    def handle_at(self, px: float, py: float, tol: float = 10.0) -> str | None:
        if math.hypot(px - self.x0, py - self.y0) <= self.radius + tol:
            return "inside"
        return None

    def move(self, dx: float, dy: float) -> None:
        self.x0 += dx
        self.y0 += dy
        self.x1 = self.x0
        self.y1 = self.y0


# --------------------------------------------------------------------------- #
# Blur / Pixelate — mutate the underlying image at flatten time
# --------------------------------------------------------------------------- #
@dataclass
class _RegionEffect(Annotation):
    def _region_box(self, w: int, h: int) -> tuple[int, int, int, int] | None:
        b = self.bounds().clamp(Rect(0, 0, w, h))
        if b.is_empty(2):
            return None
        return (b.x, b.y, b.x1, b.y1)

    def draw(self, cr, scale: float = 1.0) -> None:
        # editor placeholder so the user can see/move the region
        cr.save()
        b = self.bounds()
        cr.set_source_rgba(0.2, 0.2, 0.2, 0.45)
        cr.rectangle(b.x * scale, b.y * scale, b.w * scale, b.h * scale)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.7)
        cr.set_line_width(1.0)
        cr.set_dash([4, 3])
        cr.rectangle(b.x * scale, b.y * scale, b.w * scale, b.h * scale)
        cr.stroke()
        cr.restore()


@dataclass
class BlurTool(_RegionEffect):
    radius: float = 8.0
    kind: str = "blur"

    def apply_to_image(self, base_img) -> None:
        from PIL import ImageFilter

        box = self._region_box(base_img.width, base_img.height)
        if box is None:
            return
        region = base_img.crop(box)
        region = region.filter(ImageFilter.GaussianBlur(radius=self.radius))
        base_img.paste(region, box)


@dataclass
class PixelateTool(_RegionEffect):
    block: int = 12
    kind: str = "pixelate"

    def apply_to_image(self, base_img) -> None:
        from PIL import Image

        box = self._region_box(base_img.width, base_img.height)
        if box is None:
            return
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        region = base_img.crop(box)
        bw = max(1, w // max(1, self.block))
        bh = max(1, h // max(1, self.block))
        small = region.resize((bw, bh), Image.NEAREST)
        region = small.resize((w, h), Image.NEAREST)
        base_img.paste(region, box)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
#: tool key -> class.  Order matches the toolbar / shortcut layout.
REGISTRY: dict[str, type[Annotation]] = {
    "arrow": ArrowTool,
    "line": LineTool,
    "rect": RectTool,
    "ellipse": EllipseTool,
    "text": TextTool,
    "highlight": HighlightTool,
    "counter": CounterTool,
    "blur": BlurTool,
    "pixelate": PixelateTool,
}

#: single-key shortcut -> tool key (V/K handled by the editor as select/crop)
SHORTCUTS: dict[str, str] = {
    "a": "arrow",
    "l": "line",
    "r": "rect",
    "e": "ellipse",
    "t": "text",
    "h": "highlight",
    "c": "counter",
    "b": "blur",
    "p": "pixelate",
}


def make(kind: str, **kwargs) -> Annotation:
    """Construct an annotation by registry key."""
    cls = REGISTRY[kind]
    return cls(**kwargs)


# --------------------------------------------------------------------------- #
# Undo/redo — snapshot the whole object list
# --------------------------------------------------------------------------- #
class History:
    """Snapshot-based undo/redo over a list of annotation objects.

    We deep-copy the list on each push so undo restores prior *state* (geometry,
    colour, counter numbers) and not just membership.  Cheap: annotations are
    tiny dataclasses.
    """

    def __init__(self) -> None:
        self._undo: list[list[Annotation]] = []
        self._redo: list[list[Annotation]] = []

    def push(self, objects: list[Annotation]) -> None:
        self._undo.append(copy.deepcopy(objects))
        self._redo.clear()

    def can_undo(self) -> bool:
        return len(self._undo) > 1

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self, current: list[Annotation]) -> list[Annotation] | None:
        if len(self._undo) <= 1:
            return None
        self._redo.append(self._undo.pop())
        return copy.deepcopy(self._undo[-1])

    def redo(self) -> list[Annotation] | None:
        if not self._redo:
            return None
        snap = self._redo.pop()
        self._undo.append(copy.deepcopy(snap))
        return copy.deepcopy(snap)


# --------------------------------------------------------------------------- #
# Small math helpers
# --------------------------------------------------------------------------- #
def _point_near_segment(px, py, x0, y0, x1, y1, tol) -> bool:
    dx, dy = x1 - x0, y1 - y0
    if dx == 0 and dy == 0:
        return math.hypot(px - x0, py - y0) <= tol
    t = ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = x0 + t * dx, y0 + t * dy
    return math.hypot(px - cx, py - cy) <= tol


__all__ = [
    "Annotation",
    "ArrowTool",
    "LineTool",
    "RectTool",
    "EllipseTool",
    "TextTool",
    "HighlightTool",
    "CounterTool",
    "BlurTool",
    "PixelateTool",
    "REGISTRY",
    "SHORTCUTS",
    "History",
    "make",
]
