#!/usr/bin/env python3
"""Headed test: show RegionSelector on a known image, print the rect on_done.

Driven externally by ydotool. Writes the resulting rect to /tmp/clipshot_rect.txt
so the harness can assert on it. Auto-times-out so it never hangs the screen.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from PIL import Image  # noqa: E402
from clipshot.config import Config  # noqa: E402
from clipshot.region_selector import RegionSelector  # noqa: E402

OUT = Path("/tmp/clipshot_rect.txt")
IMG = Path("/tmp/clipshot_seltest.png")


def main():
    # A test image sized to a common logical screen so scale is ~1.
    Image.new("RGBA", (1920, 1080), (40, 44, 52, 255)).save(IMG)
    OUT.write_text("PENDING")
    app = Adw.Application(application_id="uk.florinlab.ClipShot.SelTest")

    def on_done(rect):
        OUT.write_text("CANCELLED" if rect is None else f"{rect.x},{rect.y},{rect.w},{rect.h}")
        app.quit()

    def on_activate(a):
        sel = RegionSelector(a, IMG, Config(), on_done)
        sel.present()
        GLib.timeout_add_seconds(12, lambda: (OUT.write_text("TIMEOUT"), app.quit())[1])

    app.connect("activate", on_activate)
    app.run([])


if __name__ == "__main__":
    main()
