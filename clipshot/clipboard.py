"""Clipboard backends.

Wayland is the primary target (wl-copy).  We deliberately shell out to wl-copy
instead of using GTK's clipboard because:
  * wl-copy forks and keeps serving the offer after our window closes — solving
    the "clipboard dies with the owner" Wayland gotcha for free;
  * it lets us set an explicit image/png MIME type so it pastes into GIMP /
    browsers / Slack reliably (the recurring Flameshot-on-GNOME failure).

X11 fallback uses xclip.  Backend is auto-detected from the session.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class ClipboardError(RuntimeError):
    pass


def _is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) and os.environ.get(
        "XDG_SESSION_TYPE", ""
    ) != "x11"


def detect_backend(prefer: str = "auto") -> str:
    if prefer in ("wayland", "x11"):
        return prefer
    return "wayland" if _is_wayland() else "x11"


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _spawn_detached(argv: list[str], data: bytes, err_label: str) -> None:
    """Feed bytes to a clipboard helper that forks to serve the offer.

    start_new_session=True puts the helper in its own session/process-group so it
    survives even if *our* process group is signalled — the Wayland "clipboard
    owner dies, clipboard clears" failure mode then can't be triggered by us.
    """
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _, stderr = proc.communicate(input=data, timeout=15)
    except subprocess.TimeoutExpired:
        # wl-copy daemonised before reaching EOF handling — that's fine, it's serving.
        return
    if proc.returncode not in (0, None):
        raise ClipboardError(f"{err_label} failed: {stderr.decode(errors='replace')}")


def copy_image(png_bytes: bytes, prefer: str = "auto") -> None:
    """Put a PNG on the clipboard so it can be pasted as an image anywhere."""
    backend = detect_backend(prefer)
    if backend == "wayland":
        if not _have("wl-copy"):
            raise ClipboardError("wl-copy not found (install wl-clipboard)")
        _spawn_detached(["wl-copy", "--type", "image/png"], png_bytes, "wl-copy")
    else:
        if not _have("xclip"):
            raise ClipboardError("xclip not found (install xclip)")
        _spawn_detached(
            ["xclip", "-selection", "clipboard", "-t", "image/png"], png_bytes, "xclip")


def copy_image_file(path: str | Path, prefer: str = "auto") -> None:
    with open(path, "rb") as fh:
        copy_image(fh.read(), prefer=prefer)


def copy_text(text: str, prefer: str = "auto") -> None:
    """Put plain text on the clipboard (used by OCR)."""
    backend = detect_backend(prefer)
    if backend == "wayland":
        if not _have("wl-copy"):
            raise ClipboardError("wl-copy not found (install wl-clipboard)")
        subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True)
    else:
        if not _have("xclip"):
            raise ClipboardError("xclip not found (install xclip)")
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text.encode("utf-8"),
            check=True,
        )


def paste_image_bytes(prefer: str = "auto") -> bytes:
    """Read an image/png back off the clipboard — used by tests to verify a copy."""
    backend = detect_backend(prefer)
    if backend == "wayland":
        out = subprocess.run(
            ["wl-paste", "--type", "image/png"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return out.stdout
    out = subprocess.run(
        ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return out.stdout
