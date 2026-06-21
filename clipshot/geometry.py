"""Pure geometry helpers — no GUI, fully unit-testable.

Coordinates are in *image pixels* (the captured full-screen still), not logical
GTK units.  The region selector converts widget coords -> image coords before
handing a Rect here.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @classmethod
    def from_points(cls, x0: float, y0: float, x1: float, y1: float) -> "Rect":
        """Build a normalised (non-negative w/h) rect from two drag points."""
        x = int(round(min(x0, x1)))
        y = int(round(min(y0, y1)))
        w = int(round(abs(x1 - x0)))
        h = int(round(abs(y1 - y0)))
        return cls(x, y, w, h)

    @property
    def x1(self) -> int:
        return self.x + self.w

    @property
    def y1(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return self.w * self.h

    def is_empty(self, min_side: int = 1) -> bool:
        return self.w < min_side or self.h < min_side

    def clamp(self, bounds: "Rect") -> "Rect":
        """Clamp this rect to stay inside bounds (e.g. the screen)."""
        x = max(bounds.x, min(self.x, bounds.x1))
        y = max(bounds.y, min(self.y, bounds.y1))
        x1 = max(bounds.x, min(self.x1, bounds.x1))
        y1 = max(bounds.y, min(self.y1, bounds.y1))
        return Rect(x, y, max(0, x1 - x), max(0, y1 - y))

    def translated(self, dx: int, dy: int) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def grown(self, edges: str, dx: int, dy: int) -> "Rect":
        """Resize by dragging a named edge/corner handle.

        edges is any combination of n/s/e/w (e.g. 'nw', 'se', 'e').
        """
        x, y, x1, y1 = self.x, self.y, self.x1, self.y1
        if "w" in edges:
            x += dx
        if "e" in edges:
            x1 += dx
        if "n" in edges:
            y += dy
        if "s" in edges:
            y1 += dy
        # keep normalised
        return Rect.from_points(x, y, x1, y1)

    def with_aspect(self, ratio: float) -> "Rect":
        """Return a rect with the same top-left but constrained to w/h == ratio.

        ratio = w / h.  Used when Shift locks aspect ratio while resizing.
        """
        if ratio <= 0:
            return self
        # honour the larger dimension to avoid shrinking unexpectedly
        if self.w / max(1, self.h) > ratio:
            new_h = int(round(self.w / ratio))
            return Rect(self.x, self.y, self.w, new_h)
        new_w = int(round(self.h * ratio))
        return Rect(self.x, self.y, new_w, self.h)


# Handle hit-testing -------------------------------------------------------

HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")


def handle_at(rect: Rect, px: float, py: float, tol: float = 10.0) -> str | None:
    """Return which resize handle (if any) the point is over, else None.

    'inside' is returned when the point is within the rect but not on a handle
    (used to move the whole selection).
    """
    def near(a: float, b: float) -> bool:
        return abs(a - b) <= tol

    cx = (rect.x + rect.x1) / 2
    cy = (rect.y + rect.y1) / 2
    on_left = near(px, rect.x)
    on_right = near(px, rect.x1)
    on_top = near(py, rect.y)
    on_bottom = near(py, rect.y1)
    mid_x = near(px, cx)
    mid_y = near(py, cy)
    in_x = rect.x - tol <= px <= rect.x1 + tol
    in_y = rect.y - tol <= py <= rect.y1 + tol

    if not (in_x and in_y):
        return None
    if on_top and on_left:
        return "nw"
    if on_top and on_right:
        return "ne"
    if on_bottom and on_left:
        return "sw"
    if on_bottom and on_right:
        return "se"
    if on_top and mid_x:
        return "n"
    if on_bottom and mid_x:
        return "s"
    if on_left and mid_y:
        return "w"
    if on_right and mid_y:
        return "e"
    if rect.x <= px <= rect.x1 and rect.y <= py <= rect.y1:
        return "inside"
    return None
