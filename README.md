# ClipShot

**ClipShot** is a CleanShot-class screenshot and screen-capture tool built for **Linux / Wayland**, with GNOME 50 as the primary target. It solves the problems that make every other Linux screenshot tool feel half-finished on a modern Wayland session.

---

## What ClipShot is

Most Linux screenshot tools were written for X11. On Wayland they either fall back to a slow portal dialog, can't pin images on screen, or lose the clipboard the moment they exit. ClipShot is built Wayland-first:

- Captures via the **XDG Screenshot Portal** (no compositor-specific hacks needed), or the optional GNOME Shell extension for extra speed and per-window precision.
- The full-screen grab is taken first, then **frozen** as a still image; region selection happens on that still — no screen flickering, no compositor tearing.
- The clipboard offer is kept alive by a **persistent daemon** process (`clipshot --daemon`) so pasted images survive even after the selection window closes.
- A floating **HUD thumbnail** appears in a corner after capture with one-click actions, styled with GTK4 + libadwaita.

---

## Feature list

| Feature | Details |
|---|---|
| Region capture | Crosshair + magnifier + live dimensions; keyboard-nudge handles |
| Fullscreen capture | Instant, no dialog |
| Window capture | Via GNOME Shell extension (falls back to region select) |
| OCR (extract text) | Tesseract, result copied to clipboard |
| Repeat last region | Re-shoot the same rect with a single hotkey |
| Timer capture | `--timer=N` — count-down then region select |
| Floating HUD | Thumbnail card (corner-configurable), auto-close optional |
| Annotation editor | Arrow, line, rect, ellipse, text, highlighter, blur, counter, crop (cairo-rendered) |
| Pin to screen | Always-on-top borderless overlay; drag, resize, opacity control |
| History | Scrollable grid, copy/pin/annotate/delete/reveal |
| Tray icon | StatusNotifierItem (works with AppIndicator GNOME extension) |
| GNOME hotkeys | Registered via gsettings, fully configurable in Settings UI |
| Mouse button binding | input-remapper preset (BTN_SIDE → region capture) |

---

## Architecture

```
hotkey / mouse button
        │
        ▼
clipshot --region  (short-lived CLI invocation)
        │   GIO single-instance → routes to running daemon
        ▼
ClipShotApp (Adw.Application, persistent daemon)
        │
        ├─ capture full screen async (XDG portal  OR  Shell extension D-Bus)
        │
        ├─ RegionSelector window  (full-screen overlay on the frozen still)
        │       crosshair · magnifier · dimension label · resize handles
        │
        ├─ imaging.crop(PIL image, rect)
        │
        ├─ clipboard.copy_image(png_bytes)   ← wl-copy detached process
        │                                        (offer stays alive in daemon)
        ├─ history.add_entry(...)
        │
        └─ HudWindow  (floating corner card)
                │
                ├─ Copy · Save · Annotate · Pin · Close
                └─ AnnotationEditor  /  PinWindow
```

**Optional layer — GNOME Shell extension** (`clipshot@florinlab.uk`):

The extension owns the D-Bus name `uk.florinlab.ClipShot` and exposes:
- `CaptureScreen()` — shell-internal full-frame capture (faster, no portal dialog)
- `CaptureActiveWindow()` — exact window geometry
- `SelectArea()` — shell-actor crosshair (reserved for future use)

ClipShot degrades gracefully without it; the extension is an optional fidelity upgrade.

---

## Screenshots

> _To be added once the annotation editor and HUD are in their final visual state._

---

## Installation

### One-liner (clone + install)

```bash
git clone https://github.com/florinhrib/clipshot.git
cd clipshot
./install.sh
```

The installer:
1. Detects your distro (dnf / apt / pacman) and installs **system packages**: PyGObject, GTK4, libadwaita, wl-clipboard, tesseract.
2. `pip install --user .` → `~/.local/bin/clipshot`.
3. Registers GNOME keyboard shortcuts via `gsettings`.
4. Installs and enables the **systemd user service** (`~/.config/systemd/user/clipshot.service`).
5. Copies the **autostart .desktop** to `~/.config/autostart/`.
6. Installs the **GNOME Shell extension**.

> **PyGObject must come from your OS package manager**, not pip. The installer handles this; do not `pip install PyGObject` manually.

### Manual install

```bash
# 1. System packages (Fedora)
sudo dnf install python3-gobject gtk4 libadwaita wl-clipboard tesseract tesseract-langpack-eng

# 2. Python package
pip install --user .

# 3. Shortcuts (optional, requires GNOME session)
python3 -c "from clipshot.config import Config; from clipshot import shortcuts; shortcuts.apply_all(Config.load())"

# 4. Service
mkdir -p ~/.config/systemd/user
cp data/clipshot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now clipshot.service

# 5. Autostart
cp data/clipshot-daemon.desktop ~/.config/autostart/

# 6. Shell extension (optional)
cp -r extension ~/.local/share/gnome-shell/extensions/clipshot@florinlab.uk
gnome-extensions enable clipshot@florinlab.uk
```

---

## Usage

```
clipshot [--daemon]          Start the tray daemon (run at login via service)
clipshot --region            Capture a region  (default)
clipshot --fullscreen        Capture the whole screen
clipshot --window            Capture the active window
clipshot --ocr               Extract text from a region
clipshot --previous          Repeat the last region
clipshot --timer=5           Region capture after 5 s
clipshot --settings          Open settings window
clipshot --history           Open capture history
```

### Default hotkeys

| Action | Default binding |
|---|---|
| Region capture | `Super + Shift + S` |
| Fullscreen | `Super + Shift + F` |
| Window | `Super + Shift + W` |
| Extract text (OCR) | `Super + Shift + T` |
| Repeat last | `Super + Shift + R` |

All bindings are configurable in **Settings → Shortcuts**.

### Mouse button binding

Import `presets/input-remapper/clipshot.json` in **input-remapper-gtk** to map the side button (`BTN_SIDE`) to region capture. Requires the `input-remapper` service:

```bash
sudo systemctl enable --now input-remapper
```

---

## Configuration

Settings are stored in `~/.config/clipshot/config.json` and editable via the Settings UI (`clipshot --settings`). Key options:

| Key | Default | Description |
|---|---|---|
| `copy_to_clipboard` | `true` | Auto-copy image on capture |
| `save_to_disk` | `false` | Also write a file |
| `save_dir` | `~/Pictures/Screenshots` | Where files land |
| `image_format` | `png` | `png` or `jpg` |
| `show_hud` | `true` | Floating thumbnail after capture |
| `hud_corner` | `bottom-left` | Where the HUD appears |
| `hud_autoclose_seconds` | `0` | 0 = keep until dismissed |
| `ocr_lang` | `eng` | Tesseract language code |
| `history_max_items` | `200` | How many captures to keep |
| `capture_backend` | `auto` | `auto` \| `extension` \| `portal` |

---

## GNOME / Wayland constraints ClipShot solves

| Problem | ClipShot solution |
|---|---|
| `grim` loses clipboard when it exits | Daemon keeps the `wl-copy` offer alive |
| Portal dialog appears mid-screen-grab | Freeze the full screen first, overlay region selector on the still |
| No layer-shell access for region overlay | Use a maximised GTK4 window with `set_decorated(False)` + compositor hints |
| Per-window capture geometry not available | GNOME Shell extension exposes it over D-Bus |
| First portal use requires manual permission grant | Shown once; ClipShot calls `xdg-desktop-portal` which prompts GNOME's built-in dialog |

---

## Troubleshooting

**Tray icon not visible**

The tray uses `org.kde.StatusNotifierItem`. GNOME hides these by default. Install the AppIndicator extension:

```
https://extensions.gnome.org/extension/615/
```

or via the command line:

```bash
gnome-extensions install appindicatorsupport@rgcjonas.gmail.com
```

**Portal permission prompt on first run**

On first use, GNOME will show a one-time "Allow ClipShot to take screenshots?" dialog. Grant it. The permission is stored in `~/.local/share/xdg-desktop-portal/`.

**OCR returns nothing**

Make sure Tesseract is installed and the English language pack is present:

```bash
# Fedora
sudo dnf install tesseract tesseract-langpack-eng
# Debian / Ubuntu
sudo apt install tesseract-ocr tesseract-ocr-eng
```

Check with: `tesseract --list-langs`

**Shortcuts not working after install**

Open GNOME Settings → Keyboard → Keyboard Shortcuts → Custom Shortcuts. The five ClipShot shortcuts should be listed. If missing, run:

```bash
python3 -c "from clipshot.config import Config; from clipshot import shortcuts; shortcuts.apply_all(Config.load())"
```

**Service fails to start**

```bash
systemctl --user status clipshot.service
journalctl --user -u clipshot.service -n 50
```

Ensure `~/.local/bin` is on your `PATH` — the service calls `clipshot` by name.

---

## Development

```bash
git clone https://github.com/florinhrib/clipshot.git
cd clipshot

# system deps (Fedora)
sudo dnf install python3-gobject gtk4 libadwaita wl-clipboard tesseract tesseract-langpack-eng

# editable install
pip install --user -e ".[dev]"

# run tests (headless-safe; GTK tests are guarded)
pytest
```

### Running tests

```bash
pytest                    # all tests
pytest tests/test_geometry.py   # one module
pytest -k "ocr"           # filter by name
```

Tests in `tests/` are importable without a display — GTK imports are guarded with `DISPLAY`/`WAYLAND_DISPLAY` checks. The OCR, imaging, geometry, and history modules are fully headless-testable.

### Project layout

```
clipshot/              Python package (daemon + all modules)
  app.py               Entry point; ClipShotApp(Adw.Application)
  config.py            Config (JSON, headless)
  capture.py           Screen capture (portal / extension)
  region_selector.py   Full-screen overlay for region selection
  imaging.py           Pillow helpers (headless)
  clipboard.py         wl-copy / xclip wrapper (headless)
  geometry.py          Rect dataclass (headless)
  ocr.py               Tesseract wrapper (headless)
  history.py           History storage + HistoryWindow
  hud.py               Floating HUD thumbnail
  annotate/            Annotation editor (cairo)
  pin.py               Always-on-top pin window
  tray.py              StatusNotifierItem D-Bus tray
  shortcuts.py         gsettings hotkey registration
  settings_ui.py       Adw.PreferencesWindow
  windows.py           Shell-extension D-Bus window helper

data/
  clipshot.desktop     Application launcher
  clipshot-daemon.desktop  Autostart entry
  clipshot.service     systemd user unit
  icons/               SVG + PNG icons

extension/             GNOME Shell extension (JS, optional)
presets/
  input-remapper/      Mouse-button preset (BTN_SIDE → region)
tests/                 pytest suite (headless)
install.sh             Distro-aware installer
uninstall.sh           Uninstaller (--purge to wipe config)
```

---

## License

MIT — see [LICENSE](LICENSE).

Copyright (c) 2026 Florin Hrib.
