"""Settings window — Adw.PreferencesWindow wrapping every key in config.DEFAULTS.

Built from Adw rows so it feels native on GNOME 50.  All changes write through
``app.cfg[key] = value; app.cfg.save()`` immediately (no Apply button needed).
Hotkey rows additionally call ``clipshot.shortcuts.apply_all(app.cfg)`` so
bindings are re-registered on every keystroke change.

GTK / Adw are lazy-imported inside the class so the module can be imported
in a headless test environment (import guard at the bottom of the file).
"""
from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402


class SettingsWindow(Adw.PreferencesWindow):
    """Preferences window for ClipShot.

    Groups mirror the logical sections in config.DEFAULTS:
    General, Capture, Selector, HUD, Shortcuts, Power, Backends.
    """

    def __init__(self, app: Any) -> None:
        super().__init__(
            title="ClipShot Settings",
            search_enabled=True,
        )
        self._app = app
        self._cfg = app.cfg

        self._build_general_page()
        self._build_capture_page()
        self._build_selector_page()
        self._build_hud_page()
        self._build_shortcuts_page()
        self._build_power_page()
        self._build_backends_page()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _write(self, key: str, value: Any) -> None:
        """Persist a single setting change."""
        self._cfg[key] = value
        self._cfg.save()

    def _write_and_apply_shortcuts(self, key: str, value: Any) -> None:
        self._write(key, value)
        try:
            from . import shortcuts
            shortcuts.apply_all(self._cfg)
        except Exception:
            pass

    # --- row factories ---------------------------------------------------

    def _switch_row(self, title: str, key: str, subtitle: str = "") -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_active(bool(self._cfg[key]))
        row.connect("notify::active", lambda r, _: self._write(key, r.get_active()))
        return row

    def _entry_row(self, title: str, key: str, subtitle: str = "") -> Adw.EntryRow:
        row = Adw.EntryRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_text(str(self._cfg.get(key, "")))
        row.connect("changed", lambda r: self._write(key, r.get_text()))
        return row

    def _hotkey_row(self, title: str, key: str) -> Adw.EntryRow:
        row = Adw.EntryRow(title=title)
        row.set_text(str(self._cfg.get(key, "")))
        row.connect("changed", lambda r: self._write_and_apply_shortcuts(key, r.get_text()))
        return row

    def _combo_row(self, title: str, key: str, options: list[str],
                   subtitle: str = "") -> Adw.ComboRow:
        model = Gtk.StringList.new(options)
        row = Adw.ComboRow(title=title, model=model)
        if subtitle:
            row.set_subtitle(subtitle)
        current = str(self._cfg.get(key, options[0]))
        if current in options:
            row.set_selected(options.index(current))
        row.connect(
            "notify::selected",
            lambda r, _: self._write(key, options[r.get_selected()]),
        )
        return row

    def _spin_row(self, title: str, key: str, lo: float, hi: float,
                  step: float = 1.0, subtitle: str = "") -> Adw.SpinRow:
        adj = Gtk.Adjustment(
            value=float(self._cfg.get(key, lo)),
            lower=lo, upper=hi, step_increment=step,
        )
        row = Adw.SpinRow(title=title, adjustment=adj, digits=0 if step >= 1 else 2)
        if subtitle:
            row.set_subtitle(subtitle)
        row.connect("changed", lambda r: self._write(key, r.get_value()))
        return row

    def _opacity_row(self, title: str, key: str) -> Adw.ActionRow:
        """ActionRow containing a Gtk.Scale for dim_opacity (0.0–1.0)."""
        row = Adw.ActionRow(title=title)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.05)
        scale.set_value(float(self._cfg.get(key, 0.45)))
        scale.set_hexpand(True)
        scale.set_valign(Gtk.Align.CENTER)
        scale.connect("value-changed", lambda s: self._write(key, round(s.get_value(), 2)))
        row.add_suffix(scale)
        return row

    def _color_row(self, title: str, key: str) -> Adw.ActionRow:
        """ActionRow with a Gtk.ColorDialogButton for selection_color."""
        row = Adw.ActionRow(title=title)

        dialog = Gtk.ColorDialog(title="Pick colour", with_alpha=False)
        btn = Gtk.ColorDialogButton(dialog=dialog)

        # Parse stored hex colour
        rgba = Gdk.RGBA()
        hex_color = str(self._cfg.get(key, "#3584e4"))
        if not rgba.parse(hex_color):
            rgba.parse("#3584e4")
        btn.set_rgba(rgba)

        def _on_color(button: Gtk.ColorDialogButton, _param: Any) -> None:
            c = button.get_rgba()
            r = int(c.red * 255)
            g = int(c.green * 255)
            b = int(c.blue * 255)
            self._write(key, f"#{r:02x}{g:02x}{b:02x}")

        btn.connect("notify::rgba", _on_color)
        btn.set_valign(Gtk.Align.CENTER)
        row.add_suffix(btn)
        return row

    # ------------------------------------------------------------------
    # pages
    # ------------------------------------------------------------------

    def _build_general_page(self) -> None:
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        self.add(page)

        grp = Adw.PreferencesGroup(title="Output")
        page.add(grp)
        grp.add(self._switch_row("Copy to clipboard", "copy_to_clipboard",
                                 "Copy the screenshot immediately after capture"))
        grp.add(self._switch_row("Save to disk", "save_to_disk",
                                 "Also write a file every time you capture"))

        grp2 = Adw.PreferencesGroup(title="File")
        page.add(grp2)
        grp2.add(self._entry_row("Save directory", "save_dir"))
        grp2.add(self._entry_row("Filename template", "filename_template",
                                 "{date} and {time} are replaced automatically"))
        grp2.add(self._combo_row("Image format", "image_format", ["png", "jpg"]))

    def _build_capture_page(self) -> None:
        page = Adw.PreferencesPage(title="Capture", icon_name="camera-photo-symbolic")
        self.add(page)

        grp = Adw.PreferencesGroup(title="Behaviour")
        page.add(grp)
        grp.add(self._switch_row("Freeze screen", "freeze_screen",
                                 "Capture a frozen still before showing the selector"))
        grp.add(self._switch_row("Hide cursor", "hide_cursor"))
        grp.add(self._spin_row("Self-timer (seconds)", "self_timer_seconds",
                               0, 30, subtitle="0 = disabled"))

    def _build_selector_page(self) -> None:
        page = Adw.PreferencesPage(title="Selector", icon_name="crosshairs-symbolic")
        self.add(page)

        grp = Adw.PreferencesGroup(title="Overlays")
        page.add(grp)
        grp.add(self._switch_row("Show magnifier", "show_magnifier"))
        grp.add(self._switch_row("Show crosshair", "show_crosshair"))
        grp.add(self._switch_row("Show dimensions", "show_dimensions"))

        grp2 = Adw.PreferencesGroup(title="Appearance")
        page.add(grp2)
        grp2.add(self._color_row("Selection colour", "selection_color"))
        grp2.add(self._opacity_row("Dim opacity", "dim_opacity"))

    def _build_hud_page(self) -> None:
        page = Adw.PreferencesPage(title="HUD", icon_name="notifications-symbolic")
        self.add(page)

        grp = Adw.PreferencesGroup(title="Floating thumbnail")
        page.add(grp)
        grp.add(self._switch_row("Show HUD after capture", "show_hud"))
        grp.add(self._combo_row("Corner", "hud_corner",
                                ["bottom-left", "bottom-right", "top-left", "top-right"]))
        grp.add(self._spin_row("Auto-close (seconds)", "hud_autoclose_seconds",
                               0, 120, subtitle="0 = never"))
        grp.add(self._combo_row("Auto-close action", "hud_autoclose_action",
                                ["discard", "save"]))

    def _build_shortcuts_page(self) -> None:
        page = Adw.PreferencesPage(title="Shortcuts", icon_name="input-keyboard-symbolic")
        self.add(page)

        grp = Adw.PreferencesGroup(
            title="Hotkeys",
            description="Use GNOME accelerator syntax, e.g. <Super><Shift>s",
        )
        page.add(grp)
        grp.add(self._hotkey_row("Region capture", "hotkey_region"))
        grp.add(self._hotkey_row("Fullscreen capture", "hotkey_fullscreen"))
        grp.add(self._hotkey_row("Window capture", "hotkey_window"))
        grp.add(self._hotkey_row("Extract text (OCR)", "hotkey_ocr"))
        grp.add(self._hotkey_row("Repeat last region", "hotkey_previous"))

    def _build_power_page(self) -> None:
        page = Adw.PreferencesPage(title="Power", icon_name="star-symbolic")
        self.add(page)

        grp = Adw.PreferencesGroup(title="OCR")
        page.add(grp)
        grp.add(self._entry_row("Tesseract language", "ocr_lang",
                                "e.g. eng, deu, fra — must be installed"))

        grp2 = Adw.PreferencesGroup(title="History")
        page.add(grp2)
        grp2.add(self._switch_row("Enable history", "history_enabled"))
        grp2.add(self._spin_row("Keep items", "history_max_items", 10, 2000))

        grp3 = Adw.PreferencesGroup(title="Pin window")
        page.add(grp3)
        grp3.add(self._switch_row("Drop shadow", "pin_shadow"))
        grp3.add(self._switch_row("Rounded corners", "pin_rounded"))

    def _build_backends_page(self) -> None:
        page = Adw.PreferencesPage(title="Backends", icon_name="applications-system-symbolic")
        self.add(page)

        grp = Adw.PreferencesGroup(
            title="Capture backend",
            description="'auto' prefers the GNOME Shell extension, then the XDG portal",
        )
        page.add(grp)
        grp.add(self._combo_row("Capture backend", "capture_backend",
                                ["auto", "extension", "portal"]))

        grp2 = Adw.PreferencesGroup(
            title="Clipboard backend",
            description="'auto' detects Wayland (wl-copy) or X11 (xclip)",
        )
        page.add(grp2)
        grp2.add(self._combo_row("Clipboard backend", "clipboard_backend",
                                 ["auto", "wayland", "x11"]))
