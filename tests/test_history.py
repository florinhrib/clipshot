"""Headless tests for clipshot.history storage functions.

These tests exercise add_entry / list_entries / delete_entry without a display.
HistoryWindow (GTK) is NOT instantiated here.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from PIL import Image

from clipshot.app import CaptureResult
from clipshot.geometry import Rect
from clipshot.history import add_entry, delete_entry, list_entries


def _make_result(ts: float, w: int = 10, h: int = 10) -> CaptureResult:
    """Build a minimal CaptureResult with a solid-colour PIL image."""
    img = Image.new("RGBA", (w, h), (255, 0, 0, 255))
    rect = Rect(0, 0, w, h)
    return CaptureResult(image=img, rect=rect, timestamp=ts)


# ---------------------------------------------------------------------------

def test_add_entry_creates_file_and_index(tmp_path: Path):
    result = _make_result(ts=1_000.0)
    saved = add_entry(result, tmp_path, max_items=10)

    assert saved.exists(), "saved PNG should exist on disk"
    assert saved.name == "1000000.png"  # int(1000.0 * 1000)

    index = json.loads((tmp_path / "index.json").read_text())
    assert len(index) == 1
    assert index[0]["file"] == "1000000.png"
    assert index[0]["ts"] == 1_000.0
    assert index[0]["w"] == 10
    assert index[0]["h"] == 10


def test_add_entry_prunes_to_max_items(tmp_path: Path):
    """Adding 3 entries with max_items=2 must leave only the 2 newest."""
    ts_base = 1_000.0
    for i in range(3):
        result = _make_result(ts=ts_base + i)
        add_entry(result, tmp_path, max_items=2)

    # Exactly 2 PNGs on disk (oldest gone).
    png_files = list(tmp_path.glob("*.png"))
    assert len(png_files) == 2, f"expected 2 PNGs, got {[f.name for f in png_files]}"

    # The oldest file must have been deleted.
    oldest_name = f"{int((ts_base + 0) * 1000)}.png"
    assert not (tmp_path / oldest_name).exists(), "oldest PNG should be deleted"

    # Index has exactly 2 entries.
    index = json.loads((tmp_path / "index.json").read_text())
    assert len(index) == 2, f"expected 2 index entries, got {len(index)}"

    # The 2nd and 3rd timestamps should survive.
    surviving_files = {e["file"] for e in index}
    assert f"{int((ts_base + 1) * 1000)}.png" in surviving_files
    assert f"{int((ts_base + 2) * 1000)}.png" in surviving_files


def test_list_entries_newest_first(tmp_path: Path):
    ts_base = 2_000.0
    for i in range(3):
        add_entry(_make_result(ts=ts_base + i), tmp_path, max_items=10)

    entries = list_entries(tmp_path)
    assert len(entries) == 3
    # Newest should come first.
    assert entries[0]["ts"] > entries[1]["ts"] > entries[2]["ts"]


def test_list_entries_missing_dir_returns_empty(tmp_path: Path):
    missing = tmp_path / "does_not_exist"
    result = list_entries(missing)
    assert result == []


def test_list_entries_corrupt_index_returns_empty(tmp_path: Path):
    (tmp_path / "index.json").write_text("NOT VALID JSON }{")
    assert list_entries(tmp_path) == []


def test_delete_entry_removes_file_and_index(tmp_path: Path):
    result = _make_result(ts=3_000.0)
    saved = add_entry(result, tmp_path, max_items=10)

    delete_entry(saved.name, tmp_path)

    assert not saved.exists(), "PNG should be removed after delete_entry"
    index = json.loads((tmp_path / "index.json").read_text())
    assert all(e["file"] != saved.name for e in index)


def test_add_entry_upsert_same_timestamp(tmp_path: Path):
    """Calling add_entry twice with the same timestamp must not duplicate the entry."""
    result = _make_result(ts=5_000.0)
    add_entry(result, tmp_path, max_items=10)
    add_entry(result, tmp_path, max_items=10)

    index = json.loads((tmp_path / "index.json").read_text())
    assert len(index) == 1, "duplicate add_entry for same ts must upsert, not append"
