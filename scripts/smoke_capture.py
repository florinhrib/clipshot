#!/usr/bin/env python3
"""End-to-end smoke test of the capture->crop->clipboard core on a live session.

Run: python3 scripts/smoke_capture.py
Verifies: portal full-screen capture, Pillow crop, wl-copy image, wl-paste read-back.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clipshot import capture, clipboard, imaging  # noqa: E402
from clipshot.geometry import Rect  # noqa: E402


def main() -> int:
    print("1. capturing full screen via", end=" ", flush=True)
    backend = "extension" if capture.extension_available() else "portal"
    print(backend, "...", flush=True)
    path = capture.capture_fullscreen(backend="auto")
    img = imaging.load(path)
    print(f"   captured {img.width}x{img.height} -> {path}")

    print("2. cropping a 400x300 region from the top-left...")
    region = Rect(50, 50, 400, 300).clamp(Rect(0, 0, img.width, img.height))
    cropped = imaging.crop(img, region)
    png = imaging.to_png_bytes(cropped)
    print(f"   cropped {cropped.width}x{cropped.height}, {len(png)} bytes PNG")

    print("3. copying to clipboard (wl-copy image/png)...")
    clipboard.copy_image(png)

    print("4. reading back with wl-paste to verify...")
    import time
    time.sleep(0.4)  # let wl-copy claim the selection before we read
    back = clipboard.paste_image_bytes()
    ok = back[:8] == b"\x89PNG\r\n\x1a\n" and len(back) > 0
    print(f"   read back {len(back)} bytes, PNG signature: {'OK' if ok else 'MISSING'}")

    capture.cleanup_capture(path)
    if ok:
        print("\nSMOKE TEST PASSED — capture->crop->clipboard works end to end.")
        print("(Try Ctrl+V into any image-capable app to confirm visually.)")
        return 0
    print("\nSMOKE TEST FAILED — clipboard read-back did not look like a PNG.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
