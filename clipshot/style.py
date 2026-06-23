"""Shared GTK CSS registration helper.

``Gtk.StyleContext.add_provider_for_display`` attaches a provider to the display
*permanently* — it is never garbage-collected and is re-run on every restyle.
Calling it from a window constructor therefore leaks one provider per window
created, which over a long-lived daemon (every screenshot builds a HUD) piles
up hundreds of identical providers: memory grows and the style cascade gets
linearly slower, burning CPU.  This helper registers each distinct CSS blob at
most once per display, so repeated window creation is free.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

# (id(display), css_bytes) pairs already installed.  Keyed by content so the
# same stylesheet is never added twice, while genuinely different stylesheets
# each register once.
_installed: set[tuple[int, bytes]] = set()


def install_css_once(css: bytes) -> None:
    """Add *css* to the default display's style cascade, but only the first time.

    Safe to call from every window constructor: subsequent calls with the same
    bytes are no-ops, so no provider ever leaks.
    """
    display = Gdk.Display.get_default()
    if display is None:
        return
    key = (id(display), css)
    if key in _installed:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(css)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _installed.add(key)
