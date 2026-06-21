# ClipShot — module interface contracts (authoritative)

All modules live in the `clipshot/` Python package (import as `from clipshot import ...`).
Target: **Python 3.11+, PyGObject GTK4 + libadwaita (Adw 1)**, GNOME 50 / Wayland on
Fedora 44. No GTK3. Keep each module importable without a display where feasible
(lazy-import GTK inside functions/classes that need it). Match the existing code
style: type hints, module docstring explaining *why*, dataclasses where natural.

## Already implemented (DO NOT recreate — import and use)

### `clipshot/__init__.py`
- `APP_ID = "uk.florinlab.ClipShot"`, `APP_NAME = "ClipShot"`, `__version__`.

### `clipshot/geometry.py`
- `Rect(x, y, w, h)` frozen dataclass. Helpers: `.from_points(x0,y0,x1,y1)`,
  `.x1 .y1 .area`, `.is_empty(min_side=1)`, `.clamp(bounds)`, `.translated(dx,dy)`,
  `.grown(edges, dx, dy)`, `.with_aspect(ratio)`.
- `handle_at(rect, px, py, tol=10) -> "nw"|"n"|...|"inside"|None`.

### `clipshot/config.py`
- `Config` dict-like: `cfg["key"]`, `cfg.get(k, d)`, `cfg.update(**kw)`, `cfg.as_dict()`,
  `Config.load(path=CONFIG_PATH)`, `cfg.save(path=CONFIG_PATH)`.
- `CONFIG_PATH`, `CONFIG_DIR`, `DATA_DIR`, `HISTORY_DIR`. All keys + defaults are in
  `DEFAULTS` (read it). Unknown keys are dropped on load.

### `clipshot/imaging.py` (Pillow, headless)
- `load(path) -> PIL.Image (RGBA)`, `crop(img, rect) -> Image`,
  `to_png_bytes(img)`, `to_jpg_bytes(img, q)`, `encode(img, fmt)`, `save(img, path, fmt) -> Path`.

### `clipshot/clipboard.py`
- `copy_image(png_bytes, prefer="auto")`, `copy_image_file(path)`, `copy_text(text)`,
  `paste_image_bytes() -> bytes`. Auto-detects Wayland(wl-copy)/X11(xclip). Detached.

### `clipshot/capture.py`
- `capture_fullscreen(backend="auto", interactive=False) -> Path` (sync).
- `capture_fullscreen_portal_async(on_done, interactive=False)` — `on_done(path|None, error|None)`,
  runs on the live GLib loop (use this in the daemon).
- `extension_available() -> bool`, `cleanup_capture(path)`.

### `clipshot/region_selector.py`
- `RegionSelector(app, image_path, config, on_done)` — `Gtk.ApplicationWindow`.
  `on_done(rect: Rect | None)` called when user confirms (Rect) or cancels (None).
  Already does crosshair, magnifier, dimensions, handles, HiDPI mapping.

### `clipshot/app.py` — the daemon (`ClipShotApp(Adw.Application)`)
- Dataclass **`CaptureResult`**: fields `image` (PIL.Image), `rect` (Rect),
  `timestamp` (float), `saved_path` (Path|None); property `.png -> bytes`.
- Methods feature modules may call back into:
  `app.cfg` (Config), `app.notify(msg)`, `app.notify_error(msg)`,
  `app.open_annotation(result)`, `app.pin_to_screen(result)`,
  `app.open_settings()`, `app.open_history()`, `app.finish_capture(image, rect)`,
  `app.capture_region()`, `app.quit_app()`.
- Action names (Gio actions on the app): capture-region, capture-fullscreen,
  capture-window, capture-ocr, capture-previous, capture-timer, show-settings,
  show-history, about, quit. Trigger via `app.activate_action("name", None)`.

## Modules to BUILD (contracts)

### `clipshot/tray.py`
- `class Tray:` `__init__(self, app: ClipShotApp)`. Publishes a
  **StatusNotifierItem** (`org.kde.StatusNotifierItem`) + `com.canonical.dbusmenu`
  over the session bus using `Gio.DBusConnection.register_object` (NO GTK3, NO
  libappindicator). The already-installed `appindicatorsupport@rgcjonas.gmail.com`
  extension renders it. Menu items call `app.activate_action(<name>, None)`:
  Capture Region, Capture Fullscreen, Capture Window, Extract Text (OCR),
  Repeat Last, — , History, Settings, About, — , Quit. Icon name
  "camera-photo-symbolic" (themed). Must degrade gracefully if registration fails
  (log, don't crash the daemon).

### `clipshot/hud.py`
- `class HudWindow(Gtk.ApplicationWindow):` `__init__(self, app, result: CaptureResult)`.
  A small, **non-modal, undecorated, always-on-top** floating thumbnail card
  (~260px) in the corner from `app.cfg["hud_corner"]`. Shows the screenshot
  thumbnail (from `result.image` via GdkPixbuf — convert PIL→bytes→Pixbuf).
  Hover reveals a button row: Copy, Save, Annotate (`app.open_annotation(result)`),
  Pin (`app.pin_to_screen(result)`), Close. "Copy" re-copies `result.png` via
  clipboard.copy_image. "Save" writes via imaging.save to cfg save_dir.
  Support drag-to-export (DragSource providing the saved file URI) if feasible.
  Auto-close after `cfg["hud_autoclose_seconds"]` if > 0 (action per
  cfg["hud_autoclose_action"]). Rounded corners + shadow via CSS.

### `clipshot/annotate/editor.py` and `clipshot/annotate/tools.py`
- `class AnnotationEditor(Gtk.ApplicationWindow):` `__init__(self, app, result: CaptureResult)`.
  Real titled window (not overlay). Bottom toolbar. Tools (single-key shortcuts):
  V select/move, A arrow, L line, R rectangle, E ellipse, T text, H highlighter,
  C counter (auto-increment numbered badges), B blur, P pixelate, K crop.
  Color picker + stroke width. Undo/redo (Ctrl+Z / Ctrl+Shift+Z). Render with
  cairo on a Gtk.DrawingArea over the image. Each tool is an object in tools.py
  (a list of annotation objects, re-rendered each draw — keep them editable/movable).
  Export: flatten to a PIL image (render cairo to an ImageSurface, convert to PIL),
  then `clipboard.copy_image` (Ctrl+Shift+C / "Copy") and Save. Canvas auto-expands
  when an annotation extends beyond image bounds (nice-to-have).

### `clipshot/pin.py`
- `class PinWindow(Gtk.ApplicationWindow):` `__init__(self, app, result)`. Always-on-top
  borderless floating window showing the screenshot at natural size; draggable to
  move, scroll or corner-drag to resize, opacity control (scroll+modifier or a small
  slider on hover), Escape/close to dismiss, arrow keys nudge 1px. Optional rounded
  corners + shadow per cfg["pin_rounded"]/["pin_shadow"]. Multiple instances allowed.

### `clipshot/ocr.py`
- `def extract_text(image, lang="eng") -> str`. Uses `tesseract` via subprocess
  (pipe a PNG to `tesseract stdin stdout -l <lang> --psm 6`). Raise a clear
  RuntimeError with install hint if tesseract is missing (`shutil.which`).
  Headless-testable (no GTK). Also `def tesseract_available() -> bool`.

### `clipshot/windows.py`
- `def capture_active_window() -> tuple[PIL.Image|None, Rect|None]`. Talks to the
  ClipShot Shell extension D-Bus iface `uk.florinlab.ClipShot` (name `uk.florinlab.ClipShot`,
  path `/uk/florinlab/ClipShot`) method `CaptureActiveWindow() -> (s path, i x, i y, i w, i h)`.
  Returns (None, None) if extension absent. Keep imports lazy.

### `clipshot/history.py`
- `def add_entry(result: CaptureResult, history_dir: Path, max_items: int) -> Path`:
  save the PNG into history_dir as `<timestamp>.png` + append metadata to
  `history_dir/index.json` (list of {file, ts, w, h}); prune to max_items (delete old files).
- `def list_entries(history_dir=HISTORY_DIR) -> list[dict]` (newest first).
- `class HistoryWindow(Adw.Window or Gtk.ApplicationWindow):` `__init__(self, app)`.
  Grid of recent thumbnails; click → actions (Copy, Pin via app.pin_to_screen with a
  rebuilt CaptureResult, Annotate, Delete, Reveal). Keep storage funcs headless-testable.

### `clipshot/settings_ui.py`
- `class SettingsWindow(Adw.PreferencesWindow):` `__init__(self, app)`. Pages/groups
  mapping the keys in config.DEFAULTS: General (copy_to_clipboard, save_to_disk,
  save_dir, filename_template, image_format), Capture (freeze, hide_cursor, self_timer),
  Selector (magnifier, crosshair, dimensions, selection_color, dim_opacity),
  HUD (show_hud, corner, autoclose), Shortcuts (the hotkey_* keys — editing one calls
  `clipshot.shortcuts.apply_all(app.cfg)`), Backends, Power (ocr_lang, history). Writes
  through `app.cfg[...] = value; app.cfg.save()`. Use Adw rows (SwitchRow, ComboRow, etc).

### `clipshot/shortcuts.py`
- `def apply_all(cfg) -> None`: register GNOME custom keybindings via gsettings for
  each `hotkey_*` in cfg, each launching `clipshot --<action>` (region/fullscreen/
  window/ocr/previous). Idempotent: reuse stable custom-keybinding paths
  `/org/gnome/.../custom-keybindings/clipshot-<action>/`. Append to the
  custom-keybindings array without clobbering existing entries.
- `def remove_all() -> None`: remove ClipShot's keybindings.
- `def detect_desktop() -> str` ("gnome"|"kde"|"wlroots"|"other"). Only GNOME path
  implemented now; others log a TODO. Headless-testable (guard gsettings calls).

### `extension/` — GNOME Shell extension (UUID `clipshot@florinlab.uk`)
- `metadata.json` (shell-version include "45".."50"), `extension.js` (+ optional
  `dbus.js`). Owns session bus name `uk.florinlab.ClipShot` at `/uk/florinlab/ClipShot`,
  interface `uk.florinlab.ClipShot` with methods:
  `CaptureScreen() -> (s png_path)` — full screen via Shell's internal Screenshot;
  `CaptureActiveWindow() -> (s path, i x, i y, i w, i h)`;
  `SelectArea() -> (i x, i y, i w, i h)` — optional Shell-actor crosshair returning a rect.
  Implement with `imports.ui.screenshot` / `global.display`. Provide enable()/disable()
  that own/unown the name and add/remove the D-Bus object. Keep it small and robust;
  it is an OPTIONAL fidelity layer — the Python app fully works without it.

### Packaging
- `pyproject.toml` (project name "clipshot", entry point `clipshot = "clipshot.app:main"`,
  deps: pillow, dasbus; note PyGObject is a system dep). `data/clipshot.desktop`,
  `data/io... ` autostart desktop (`--daemon`), a systemd **user** unit
  `data/clipshot.service` (ExecStart=clipshot --daemon). `install.sh` (curl|bash-able):
  detect distro, install system deps (wl-clipboard, tesseract, gobject libs) via
  dnf/apt/pacman with sudo, pip-install the package to ~/.local, register shortcuts
  (call `python -m clipshot ... ` or shortcuts.apply_all), install+enable the systemd
  user service, install the GNOME extension, print the AppIndicator-extension reminder.
  `uninstall.sh` reverses it. `presets/input-remapper/clipshot.json` mapping a mouse
  side button (BTN_SIDE) to run `clipshot --region`.
```
