#!/usr/bin/env python3
"""Deterministic verification of the RegionSelector event-wiring.

Instantiates a real RegionSelector window, then drives its drag handlers the way
GtkGestureDrag would (begin + update offsets + end), plus a resize-handle drag and
arrow-key nudge, and asserts on_done receives the expected Rect. No flaky synthetic
input — this exercises the exact callbacks the gestures invoke. Exit 0 = pass.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, GLib  # noqa: E402

from PIL import Image  # noqa: E402
from clipshot.config import Config  # noqa: E402
from clipshot.geometry import Rect  # noqa: E402
from clipshot.region_selector import RegionSelector  # noqa: E402

IMG = Path("/tmp/clipshot_seltest.png")
Image.new("RGBA", (1920, 1080), (40, 44, 52, 255)).save(IMG)

results = {"rect": None, "ok": False, "msg": ""}


def run_checks(sel):
    # Force a known scale (no real allocation when unmapped): patch _scale to 1.0
    sel._scale = lambda: 1.0  # type: ignore

    # 1) draw a selection by simulating a drag begin at (700,400) and dragging
    #    by offset (+600,+400) -> end at (1300,800)
    sel._on_drag_begin(None, 700, 400)
    sel._on_drag_update(None, 600, 400)
    sel._on_drag_end(None, 600, 400)
    drawn = sel.sel
    assert drawn is not None, "no selection after drag"
    assert drawn == Rect(700, 400, 600, 400), f"draw rect wrong: {drawn}"

    # 2) move the whole selection by grabbing inside and dragging (+100,+50)
    sel._on_drag_begin(None, 1000, 600)   # inside the rect
    sel._on_drag_update(None, 100, 50)
    sel._on_drag_end(None, 100, 50)
    moved = sel.sel
    assert moved == Rect(800, 450, 600, 400), f"move rect wrong: {moved}"

    # 3) resize via the SE handle: grab the SE corner (1400,850) and drag (+50,+50)
    se = (moved.x1, moved.y1)
    sel._on_drag_begin(None, se[0], se[1])
    sel._on_drag_update(None, 50, 50)
    sel._on_drag_end(None, 50, 50)
    resized = sel.sel
    assert resized.w == 650 and resized.h == 450, f"resize wrong: {resized}"

    # 4) arrow-key nudge: move left by 1px (no modifiers)
    before = sel.sel
    sel._on_key(None, Gdk.KEY_Left, 0, 0)
    assert sel.sel.x == before.x - 1, "arrow nudge failed"

    # 5) confirm via Enter -> on_done fires with the final rect
    sel._on_key(None, Gdk.KEY_Return, 0, 0)


def main():
    app = Adw.Application(application_id="uk.florinlab.ClipShot.Verify")

    def on_done(rect):
        results["rect"] = rect

    def on_activate(a):
        sel = RegionSelector(a, IMG, Config(), on_done)
        sel.present()

        def do():
            try:
                run_checks(sel)
                results["ok"] = True
            except AssertionError as e:
                results["msg"] = str(e)
            except Exception as e:  # noqa
                results["msg"] = f"{type(e).__name__}: {e}"
            GLib.timeout_add(300, lambda: (a.quit(), False)[1])
            return False
        GLib.timeout_add(200, do)

    app.connect("activate", on_activate)
    app.run([])

    r = results["rect"]
    # after draw->move->resize(w=650,h=450)->nudge-left-1px(x:800->799)->confirm
    if results["ok"] and r is not None and r == Rect(799, 450, 650, 450):
        print(f"PASS — selector wiring verified, final rect = {r}")
        return 0
    print(f"FAIL — ok={results['ok']} rect={r} msg={results['msg']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
