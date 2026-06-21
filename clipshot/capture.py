"""Screen capture backends.

On GNOME/Mutter there is no wlr-screencopy, so the sanctioned path is the XDG
desktop portal `org.freedesktop.portal.Screenshot`.  We call it with
interactive=false to get a *full-screen still*, then the region selector lets the
user crop on that frozen image — which is also how we get CleanShot's
"freeze the screen" behaviour for free.

If the ClipShot GNOME Shell extension is present it can capture at the compositor
level (and preserve open menus); `capture_fullscreen` will prefer it when asked.
"""
from __future__ import annotations

import itertools
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENSHOT_IFACE = "org.freedesktop.portal.Screenshot"
REQUEST_IFACE = "org.freedesktop.portal.Request"
# The Shell extension owns this dedicated well-known name on the session bus.
EXT_BUS = "uk.florinlab.ClipShot"
EXT_PATH = "/uk/florinlab/ClipShot"
EXT_IFACE = "uk.florinlab.ClipShot"

_token_counter = itertools.count(1)


class CaptureError(RuntimeError):
    pass


def _uri_to_path(uri: str) -> Path:
    return Path(unquote(urlparse(uri).path))


def _session_bus() -> Gio.DBusConnection:
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def extension_available(conn: Gio.DBusConnection | None = None) -> bool:
    """True if the ClipShot Shell extension owns its bus name on the session bus.

    Uses NameHasOwner (not Peer.Ping, which succeeds for any owned name even when
    the object/interface is absent — that caused false positives).
    """
    conn = conn or _session_bus()
    try:
        reply = conn.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", (EXT_BUS,)),
            GLib.VariantType("(b)"),
            Gio.DBusCallFlags.NONE, 1000, None,
        )
        (owned,) = reply.unpack()
        return bool(owned)
    except GLib.Error:
        return False


def capture_fullscreen_portal(interactive: bool = False, timeout_ms: int = 30000) -> Path:
    """Capture the whole screen through the XDG portal. Returns a PNG path.

    Synchronous: spins a private GLib main loop, so it is safe to call from a CLI
    entrypoint.  The first call on a fresh GNOME session may show a one-time
    permission dialog; subsequent calls are silent.
    """
    conn = _session_bus()
    unique = conn.get_unique_name() or ""
    sender = unique.lstrip(":").replace(".", "_")
    token = f"clipshot_{next(_token_counter)}"
    request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    loop = GLib.MainLoop()
    result: dict[str, object] = {}

    def on_response(_conn, _sender, _path, _iface, _signal, params):
        response_code, results = params.unpack()
        if response_code != 0:
            result["error"] = f"portal returned code {response_code} (user cancelled or denied)"
        else:
            uri = results.get("uri")
            if not uri:
                result["error"] = "portal response missing uri"
            else:
                result["path"] = _uri_to_path(uri)
        loop.quit()

    sub_id = conn.signal_subscribe(
        PORTAL_BUS, REQUEST_IFACE, "Response", request_path, None,
        Gio.DBusSignalFlags.NONE, on_response,
    )

    def on_timeout():
        result["error"] = "portal screenshot timed out"
        loop.quit()
        return False

    timeout_id = GLib.timeout_add(timeout_ms, on_timeout)

    options = {
        "handle_token": GLib.Variant("s", token),
        "interactive": GLib.Variant("b", interactive),
    }
    try:
        conn.call_sync(
            PORTAL_BUS, PORTAL_PATH, SCREENSHOT_IFACE, "Screenshot",
            GLib.Variant("(sa{sv})", ("", options)),
            GLib.VariantType("(o)"),
            Gio.DBusCallFlags.NONE, timeout_ms, None,
        )
    except GLib.Error as exc:
        conn.signal_unsubscribe(sub_id)
        GLib.source_remove(timeout_id)
        raise CaptureError(f"portal Screenshot call failed: {exc.message}") from exc

    loop.run()
    conn.signal_unsubscribe(sub_id)
    GLib.source_remove(timeout_id)

    if "error" in result:
        raise CaptureError(str(result["error"]))
    path = result.get("path")
    if not path or not Path(path).exists():
        raise CaptureError("portal screenshot produced no file")
    return Path(path)  # type: ignore[arg-type]


def capture_fullscreen_portal_async(on_done, interactive: bool = False,
                                    timeout_ms: int = 30000) -> None:
    """Async portal capture that runs on the *existing* GLib/GTK main loop.

    on_done(path: Path | None, error: str | None) is invoked when the portal
    responds.  Use this from inside the daemon so we never nest main loops.
    """
    conn = _session_bus()
    unique = conn.get_unique_name() or ""
    sender = unique.lstrip(":").replace(".", "_")
    token = f"clipshot_{next(_token_counter)}"
    request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
    state: dict[str, object] = {"done": False}

    def finish(path, error):
        if state["done"]:
            return
        state["done"] = True
        try:
            conn.signal_unsubscribe(sub_id)
        except Exception:
            pass
        if state.get("timeout_id"):
            GLib.source_remove(state["timeout_id"])  # type: ignore[arg-type]
        on_done(path, error)

    def on_response(_c, _s, _p, _i, _sig, params):
        code, results = params.unpack()
        if code != 0:
            finish(None, f"cancelled or denied (code {code})")
            return
        uri = results.get("uri")
        if not uri:
            finish(None, "portal response missing uri")
            return
        finish(_uri_to_path(uri), None)

    sub_id = conn.signal_subscribe(
        PORTAL_BUS, REQUEST_IFACE, "Response", request_path, None,
        Gio.DBusSignalFlags.NONE, on_response,
    )
    state["timeout_id"] = GLib.timeout_add(
        timeout_ms, lambda: (finish(None, "timed out"), False)[1])

    options = {
        "handle_token": GLib.Variant("s", token),
        "interactive": GLib.Variant("b", interactive),
    }
    try:
        conn.call_sync(
            PORTAL_BUS, PORTAL_PATH, SCREENSHOT_IFACE, "Screenshot",
            GLib.Variant("(sa{sv})", ("", options)),
            GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, timeout_ms, None,
        )
    except GLib.Error as exc:
        finish(None, f"portal call failed: {exc.message}")


def capture_fullscreen_extension(timeout_ms: int = 5000) -> Path:
    """Capture via the ClipShot Shell extension (compositor level)."""
    conn = _session_bus()
    reply = conn.call_sync(
        EXT_BUS, EXT_PATH, EXT_IFACE, "CaptureScreen",
        None, GLib.VariantType("(s)"),
        Gio.DBusCallFlags.NONE, timeout_ms, None,
    )
    (path_str,) = reply.unpack()
    p = Path(path_str)
    if not p.exists():
        raise CaptureError("extension CaptureScreen returned a missing file")
    return p


def capture_fullscreen(backend: str = "auto", interactive: bool = False) -> Path:
    """Capture the full screen, choosing a backend.

    backend: auto | extension | portal
    Returns the path to a PNG of the whole screen.
    """
    if backend in ("auto", "extension"):
        try:
            if extension_available():
                return capture_fullscreen_extension()
        except (GLib.Error, CaptureError):
            if backend == "extension":
                raise
    return capture_fullscreen_portal(interactive=interactive)


def cleanup_capture(path: Path) -> None:
    """Remove a transient capture once we've loaded it (best effort).

    The GNOME portal writes its non-interactive screenshots either into a tmp dir
    or into ~/Pictures as Screenshot[-N].png.  Both are transients *we* requested,
    so we remove them — but we refuse to touch anything that isn't a screenshot
    file we recognise, as a guard against deleting real user files.
    """
    try:
        p = Path(path)
        tmp_roots = ("/tmp", "/run", os.environ.get("XDG_RUNTIME_DIR", "/run/user"))
        pics = str(Path(os.environ.get("XDG_PICTURES_DIR", Path.home() / "Pictures")))
        in_tmp = str(p).startswith(tmp_roots)
        in_pics_screenshot = str(p).startswith(pics) and p.name.lower().startswith("screenshot")
        if in_tmp or in_pics_screenshot:
            p.unlink(missing_ok=True)
    except OSError:
        pass
