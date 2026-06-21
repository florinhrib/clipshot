# ClipShot Helper — GNOME Shell extension

**UUID:** `clipshot@florinlab.uk`
**Target:** GNOME Shell 45–50 (Wayland/Mutter), tested on GNOME Shell 50 / Fedora 44.

This extension is an **optional fidelity layer** for the [ClipShot](https://florinlab.uk/clipshot)
screenshot app. ClipShot works perfectly well **without** it (it falls back to the XDG
desktop portal). Installing the extension lets ClipShot capture at the compositor level,
which:

- preserves open menus / popups that a portal screenshot would dismiss,
- exposes per-window geometry for "capture active window".

It owns the session bus name `uk.florinlab.ClipShot` at `/uk/florinlab/ClipShot` and
implements the `uk.florinlab.ClipShot` interface with:

| Method | Returns | Purpose |
| --- | --- | --- |
| `CaptureScreen()` | `(s pngPath)` | Full screen via `Shell.Screenshot.screenshot()`. |
| `CaptureActiveWindow()` | `(s path, i x, i y, i w, i h)` | Focused window + its frame rect. |
| `SelectArea()` | `(i x, i y, i w, i h)` | Returns `(0,0,0,0)` so the app uses its own region selector (see TODO in `extension.js`). |

Screenshots are written to PNG files under the system temp dir; ClipShot treats them
as transients and cleans them up.

## Install

### Option A — install the packaged extension

```sh
# From this directory (extension/):
gnome-extensions pack . --force
gnome-extensions install --force clipshot@florinlab.uk.shell-extension.zip
```

### Option B — symlink for development

```sh
ln -sfn "$(pwd)" ~/.local/share/gnome-shell/extensions/clipshot@florinlab.uk
```

### Then activate

On **Wayland** a full **log out / log back in** is required for the shell to pick up a
newly installed extension (you cannot restart the shell in place on Wayland). After
logging back in:

```sh
gnome-extensions enable clipshot@florinlab.uk
```

Verify it is exporting its bus name:

```sh
gdbus call --session \
  --dest uk.florinlab.ClipShot \
  --object-path /uk/florinlab/ClipShot \
  --method uk.florinlab.ClipShot.CaptureScreen
```

That should print a `('/tmp/clipshot-shell-….png',)` path.

## Uninstall

```sh
gnome-extensions disable clipshot@florinlab.uk
gnome-extensions uninstall clipshot@florinlab.uk   # or: rm the symlink
```

## Logs / debugging

```sh
journalctl --user -f -o cat /usr/bin/gnome-shell | grep -i clipshot
```
