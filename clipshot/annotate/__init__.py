"""Annotation subsystem — the CleanShot-style editor and its tool objects.

`tools` is *mostly* headless (cairo + PIL only) so the drawing/flatten path can
be unit-tested without a display; `editor` is the GTK4 window and lazy-imports gi.
"""
from __future__ import annotations
