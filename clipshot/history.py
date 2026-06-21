"""Capture history — storage (headless) + HistoryWindow (GTK4/Adw).

Storage design
--------------
Each captured image is saved as ``<int(timestamp * 1000)>.png`` inside
history_dir.  An ``index.json`` alongside it is the canonical list of
``{file, ts, w, h}`` dicts, ordered oldest-first (newest is appended).
Reads return newest-first so the UI shows the most recent item at the top.

The index write is atomic (tmp + os.replace) to survive crashes.

Circular-import avoidance
-------------------------
``CaptureResult`` lives in ``clipshot.app`` which imports GTK at module level.
Storage functions only import it lazily inside ``load_result()`` so this module
is importable and testable without a display.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .config import HISTORY_DIR
from . import imaging

if TYPE_CHECKING:
    # Only for type checkers; never executed at runtime.
    from .app import CaptureResult


# ---------------------------------------------------------------------------
# Headless storage helpers (no GTK, testable)
# ---------------------------------------------------------------------------

def add_entry(
    result: "CaptureResult",
    history_dir: Path,
    max_items: int,
) -> Path:
    """Save *result* into *history_dir* and update ``index.json``.

    Returns the Path of the saved PNG.
    Prunes the history to at most *max_items* (oldest entries deleted first).
    """
    history_dir = Path(history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)

    # Derive a filename from the millisecond timestamp — unique and sortable.
    filename = f"{int(result.timestamp * 1000)}.png"
    dest = history_dir / filename
    imaging.save(result.image, dest, "png")

    # Load (or start) the index.
    index = _load_index(history_dir)

    # Upsert: if an entry with the same file already exists (rare re-run),
    # replace it; otherwise append.
    entry = {"file": filename, "ts": result.timestamp,
             "w": result.image.width, "h": result.image.height}
    existing = [i for i, e in enumerate(index) if e.get("file") == filename]
    if existing:
        index[existing[0]] = entry
    else:
        index.append(entry)

    # Prune: drop oldest entries until we are within max_items.
    while len(index) > max_items:
        oldest = index.pop(0)
        old_path = history_dir / oldest["file"]
        try:
            old_path.unlink(missing_ok=True)
        except OSError:
            pass

    _save_index(history_dir, index)
    return dest


def list_entries(history_dir: Path = HISTORY_DIR) -> list[dict]:
    """Return history entries newest-first.  Returns [] on missing or corrupt index."""
    index = _load_index(Path(history_dir))
    return list(reversed(index))


def delete_entry(file: str, history_dir: Path = HISTORY_DIR) -> None:
    """Remove a PNG and its index entry from history_dir."""
    history_dir = Path(history_dir)
    path = history_dir / file
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    index = _load_index(history_dir)
    index = [e for e in index if e.get("file") != file]
    _save_index(history_dir, index)


def load_result(entry: dict, history_dir: Path = HISTORY_DIR) -> "CaptureResult":
    """Rebuild a CaptureResult from a stored history *entry* dict.

    CaptureResult is imported lazily here to avoid a circular import at
    module load time (clipshot.app imports GTK which requires a display for
    certain operations, but we must stay importable headlessly).
    """
    from .app import CaptureResult  # lazy — inside function only
    from .geometry import Rect

    history_dir = Path(history_dir)
    file_path = history_dir / entry["file"]
    image = imaging.load(file_path)
    rect = Rect(0, 0, entry.get("w", image.width), entry.get("h", image.height))
    return CaptureResult(
        image=image,
        rect=rect,
        timestamp=entry.get("ts", 0.0),
        saved_path=file_path,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _index_path(history_dir: Path) -> Path:
    return history_dir / "index.json"


def _load_index(history_dir: Path) -> list[dict]:
    """Load index.json, returning [] on any error."""
    try:
        with open(_index_path(history_dir), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
        return []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_index(history_dir: Path, index: list[dict]) -> None:
    """Atomically write *index* to history_dir/index.json."""
    idx_path = _index_path(history_dir)
    tmp = idx_path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)
    os.replace(tmp, idx_path)


# ---------------------------------------------------------------------------
# HistoryWindow (GTK4/Adw — only imported when a display is available)
# ---------------------------------------------------------------------------

class HistoryWindow:
    """Scrollable grid of recent capture thumbnails.

    Actual class inherits from Gtk.ApplicationWindow; GTK is imported here
    inside the class body so the module remains headlessly importable.
    """

    def __new__(cls, app):  # type: ignore[override]
        # Defer the real GTK-backed class construction until first use.
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gdk, Gio, GLib, GdkPixbuf, Gtk

        class _HistoryWindow(Gtk.ApplicationWindow):
            THUMB_SIZE = 160

            def __init__(self, app):
                super().__init__(
                    application=app,
                    title="History",
                    default_width=720,
                    default_height=540,
                )
                self._app = app
                self._history_dir = Path(app.cfg.get("history_dir", str(HISTORY_DIR)))

                # Top-level scrolled window.
                scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
                self.set_child(scroll)

                self._flow = Gtk.FlowBox(
                    valign=Gtk.Align.START,
                    max_children_per_line=10,
                    selection_mode=Gtk.SelectionMode.NONE,
                    column_spacing=8,
                    row_spacing=8,
                    margin_top=12,
                    margin_bottom=12,
                    margin_start=12,
                    margin_end=12,
                    homogeneous=True,
                )
                scroll.set_child(self._flow)
                self._refresh()

            # ------------------------------------------------------------------
            def _refresh(self):
                # Clear existing children.
                child = self._flow.get_first_child()
                while child is not None:
                    nxt = child.get_next_sibling()
                    self._flow.remove(child)
                    child = nxt

                entries = list_entries(self._history_dir)
                for entry in entries:
                    cell = self._make_cell(entry)
                    if cell is not None:
                        self._flow.append(cell)

            # ------------------------------------------------------------------
            def _make_cell(self, entry: dict):
                """Build one thumbnail cell widget for *entry*.  Returns None on error."""
                file_path = self._history_dir / entry["file"]
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        str(file_path),
                        self.THUMB_SIZE,
                        self.THUMB_SIZE,
                        preserve_aspect_ratio=True,
                    )
                except Exception:
                    return None

                # Stack: image + overlay action bar on hover.
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

                image_widget = Gtk.Image.new_from_pixbuf(pixbuf)
                image_widget.set_size_request(self.THUMB_SIZE, self.THUMB_SIZE)
                box.append(image_widget)

                # Compact action row below thumbnail.
                btn_row = Gtk.Box(
                    orientation=Gtk.Orientation.HORIZONTAL,
                    spacing=2,
                    halign=Gtk.Align.CENTER,
                )

                def _btn(label: str, callback) -> Gtk.Button:
                    b = Gtk.Button(label=label)
                    b.add_css_class("flat")
                    b.add_css_class("caption")
                    b.connect("clicked", callback)
                    return b

                btn_row.append(_btn("Copy",    lambda *_: self._copy(entry)))
                btn_row.append(_btn("Pin",     lambda *_: self._pin(entry)))
                btn_row.append(_btn("Edit",    lambda *_: self._annotate(entry)))
                btn_row.append(_btn("Delete",  lambda *_: self._delete(entry)))
                btn_row.append(_btn("Reveal",  lambda *_: self._reveal(entry)))
                box.append(btn_row)

                return box

            # ------------------------------------------------------------------
            # Action handlers
            # ------------------------------------------------------------------

            def _copy(self, entry: dict):
                from . import clipboard as cb
                file_path = self._history_dir / entry["file"]
                try:
                    with open(file_path, "rb") as fh:
                        cb.copy_image(fh.read())
                except Exception as exc:
                    print(f"[history] copy failed: {exc}")

            def _pin(self, entry: dict):
                try:
                    result = load_result(entry, self._history_dir)
                    self._app.pin_to_screen(result)
                except Exception as exc:
                    print(f"[history] pin failed: {exc}")

            def _annotate(self, entry: dict):
                try:
                    result = load_result(entry, self._history_dir)
                    self._app.open_annotation(result)
                except Exception as exc:
                    print(f"[history] annotate failed: {exc}")

            def _delete(self, entry: dict):
                delete_entry(entry["file"], self._history_dir)
                self._refresh()

            def _reveal(self, entry: dict):
                file_path = self._history_dir / entry["file"]
                try:
                    launcher = Gio.AppInfo.get_default_for_type("inode/directory", True)
                    if launcher:
                        launcher.launch_uris(
                            [GLib.filename_to_uri(str(file_path.parent), None)], None
                        )
                except Exception as exc:
                    print(f"[history] reveal failed: {exc}")

        # Construct and return the real GTK-backed instance directly.
        # Because the returned object is NOT an instance of HistoryWindow,
        # Python will NOT call HistoryWindow.__init__ after this — so we
        # initialise it here via the normal constructor call.
        return _HistoryWindow(app)

    def __init__(self, app):
        # Only reached when __new__ returns a HistoryWindow instance (never
        # in normal use); the real GTK __init__ already ran above.
        pass
