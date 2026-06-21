"""GNOME custom keybinding registration for ClipShot.

Registers/removes custom keybindings in the GNOME settings-daemon using
gsettings' relocatable schema
``org.gnome.settings-daemon.plugins.media-keys.custom-keybinding``.

Only the GNOME path is fully implemented; other desktops print a manual-bind
hint and return.  All gsettings calls go through subprocess so the module is
importable without a display, and the pure array-manipulation helpers
(_array_with, _array_without) are display/gsettings-free so unit tests can
cover them without side-effects.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from typing import Any

# --- desktop detection -------------------------------------------------------

def detect_desktop() -> str:
    """Return 'gnome' | 'kde' | 'wlroots' | 'other'."""
    for var in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP"):
        value = os.environ.get(var, "").lower()
        if "gnome" in value:
            return "gnome"
        if "kde" in value or "plasma" in value:
            return "kde"
        if "sway" in value or "hyprland" in value or "river" in value or "wlroots" in value:
            return "wlroots"
    return "other"


# --- GNOME constants ---------------------------------------------------------

_SCHEMA_BASE = "org.gnome.settings-daemon.plugins.media-keys"
_SCHEMA_BINDING = _SCHEMA_BASE + ".custom-keybinding"
_ARRAY_KEY = "custom-keybindings"
_BASE_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"

_ACTIONS: dict[str, str] = {
    "hotkey_region":     "region",
    "hotkey_fullscreen": "fullscreen",
    "hotkey_window":     "window",
    "hotkey_ocr":        "ocr",
    "hotkey_previous":   "previous",
}


# --- pure helpers (no gsettings, fully testable) ----------------------------

def _parse_array(raw: str) -> list[str]:
    """Parse the string that ``gsettings get`` returns for a string-array.

    Examples::

        '@as []'        -> []
        "['a', 'b']"    -> ['a', 'b']
        "['a']"         -> ['a']
    """
    raw = raw.strip()
    if raw in ("@as []", "[]"):
        return []
    # Strip outer brackets
    inner = re.sub(r"^\[|\]$", "", raw).strip()
    if not inner:
        return []
    # Split on commas outside quotes
    items: list[str] = []
    for part in re.findall(r"'[^']*'", inner):
        items.append(part.strip("'"))
    return items


def _array_with(paths: list[str], existing: list[str]) -> list[str]:
    """Return *existing* with every path in *paths* added (no duplicates, stable order).

    Paths already present are not re-added; existing user entries are preserved.
    """
    result = list(existing)
    for p in paths:
        if p not in result:
            result.append(p)
    return result


def _array_without(prefix: str, existing: list[str]) -> list[str]:
    """Return *existing* with every entry whose path starts with *prefix* removed."""
    return [p for p in existing if not p.startswith(prefix)]


# --- gsettings helpers -------------------------------------------------------

def _gsettings_get_array() -> list[str]:
    """Read the current custom-keybindings array from gsettings."""
    result = subprocess.run(
        ["gsettings", "get", _SCHEMA_BASE, _ARRAY_KEY],
        capture_output=True, text=True,
    )
    return _parse_array(result.stdout)


def _gsettings_set_array(paths: list[str]) -> None:
    """Write the keybindings array back to gsettings."""
    if paths:
        value = "[" + ", ".join(f"'{p}'" for p in paths) + "]"
    else:
        value = "@as []"
    subprocess.run(
        ["gsettings", "set", _SCHEMA_BASE, _ARRAY_KEY, value],
        check=True,
    )


def _gsettings_set_binding(path: str, name: str, command: str, binding: str) -> None:
    """Write name/command/binding onto the relocatable schema at *path*."""
    schema_with_path = f"{_SCHEMA_BINDING}:{path}"
    for key, val in (("name", name), ("command", command), ("binding", binding)):
        subprocess.run(
            ["gsettings", "set", schema_with_path, key, val],
            check=True,
        )


def _gsettings_reset_binding(path: str) -> None:
    """Reset all keys on the relocatable schema at *path*."""
    subprocess.run(
        ["gsettings", "reset-recursively", f"{_SCHEMA_BINDING}:{path}"],
        check=False,
    )


def _clipshot_command(action: str) -> str:
    """Return the shell command to register for *action*.

    Prefers the absolute path to the ``clipshot`` launcher; falls back to
    ``python3 -m clipshot``.
    """
    launcher = shutil.which("clipshot")
    if launcher:
        return f"{launcher} --{action}"
    return f"python3 -m clipshot --{action}"


# --- public API --------------------------------------------------------------

def apply_all(cfg: Any) -> None:
    """Register GNOME custom keybindings for every hotkey_* key in *cfg*.

    Idempotent: existing user bindings are never removed. Only clipshot-*
    paths are touched.  On non-GNOME desktops a hint is printed and the
    function returns immediately.
    """
    desktop = detect_desktop()
    if desktop != "gnome":
        cmds = " | ".join(
            f"clipshot --{action}" for action in _ACTIONS.values()
        )
        print(
            f"[clipshot] shortcuts: {desktop} not yet supported, "
            f"bind manually: {cmds}",
            file=sys.stderr,
        )
        return

    existing = _gsettings_get_array()
    new_paths: list[str] = []

    for cfg_key, action in _ACTIONS.items():
        binding = cfg.get(cfg_key, "")
        if not binding:
            continue
        path = f"{_BASE_PATH}/clipshot-{action}/"
        new_paths.append(path)
        command = _clipshot_command(action)
        _gsettings_set_binding(
            path=path,
            name=f"ClipShot {action.capitalize()}",
            command=command,
            binding=binding,
        )

    updated = _array_with(new_paths, existing)
    if updated != existing:
        _gsettings_set_array(updated)


def remove_all() -> None:
    """Remove all clipshot-* keybindings from GNOME settings-daemon.

    Idempotent: safe to call even if bindings were never registered.
    Non-GNOME desktops are silently skipped.
    """
    desktop = detect_desktop()
    if desktop != "gnome":
        return

    existing = _gsettings_get_array()
    prefix = f"{_BASE_PATH}/clipshot-"

    # Reset individual schemas before removing from the array
    for path in existing:
        if path.startswith(prefix):
            _gsettings_reset_binding(path)

    updated = _array_without(prefix, existing)
    if updated != existing:
        _gsettings_set_array(updated)
