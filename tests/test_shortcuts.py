"""Unit tests for clipshot.shortcuts pure helpers.

These tests only exercise _parse_array, _array_with, and _array_without —
no gsettings calls, no display, no subprocess.
"""
from clipshot.shortcuts import _array_with, _array_without, _parse_array

_PREFIX = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/clipshot-"
_CS_REGION = _PREFIX + "region/"
_CS_FULL = _PREFIX + "fullscreen/"
_CS_WINDOW = _PREFIX + "window/"
_USER_ENTRY = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/user-app/"


# --- _parse_array -----------------------------------------------------------

def test_parse_empty_at_as():
    assert _parse_array("@as []") == []


def test_parse_empty_brackets():
    assert _parse_array("[]") == []


def test_parse_single():
    assert _parse_array("['a']") == ["a"]


def test_parse_multiple():
    result = _parse_array("['alpha', 'beta', 'gamma']")
    assert result == ["alpha", "beta", "gamma"]


def test_parse_whitespace_tolerance():
    assert _parse_array("  [ 'x', 'y' ]  ") == ["x", "y"]


# --- _array_with ------------------------------------------------------------

def test_array_with_adds_new_paths():
    result = _array_with([_CS_REGION, _CS_FULL], [])
    assert _CS_REGION in result
    assert _CS_FULL in result


def test_array_with_preserves_user_entries():
    existing = [_USER_ENTRY]
    result = _array_with([_CS_REGION], existing)
    assert _USER_ENTRY in result
    assert _CS_REGION in result


def test_array_with_idempotent():
    existing = [_CS_REGION, _USER_ENTRY]
    result = _array_with([_CS_REGION], existing)
    assert result.count(_CS_REGION) == 1
    assert _USER_ENTRY in result


def test_array_with_stable_order():
    """Existing entries come first, new ones appended."""
    existing = [_USER_ENTRY]
    result = _array_with([_CS_REGION, _CS_FULL], existing)
    assert result[0] == _USER_ENTRY


def test_array_with_empty_paths():
    existing = [_USER_ENTRY]
    result = _array_with([], existing)
    assert result == existing


def test_array_with_all_already_present():
    existing = [_CS_REGION, _CS_FULL, _USER_ENTRY]
    result = _array_with([_CS_REGION, _CS_FULL], existing)
    assert result == existing


# --- _array_without ---------------------------------------------------------

def test_array_without_removes_matching():
    existing = [_CS_REGION, _CS_FULL, _USER_ENTRY]
    result = _array_without(_PREFIX, existing)
    assert _CS_REGION not in result
    assert _CS_FULL not in result


def test_array_without_preserves_user_entries():
    existing = [_CS_REGION, _USER_ENTRY]
    result = _array_without(_PREFIX, existing)
    assert _USER_ENTRY in result


def test_array_without_empty_list():
    result = _array_without(_PREFIX, [])
    assert result == []


def test_array_without_no_match():
    existing = [_USER_ENTRY]
    result = _array_without(_PREFIX, existing)
    assert result == existing


def test_array_without_idempotent():
    existing = [_CS_REGION, _USER_ENTRY]
    result1 = _array_without(_PREFIX, existing)
    result2 = _array_without(_PREFIX, result1)
    assert result1 == result2


def test_array_without_all_clipshot():
    existing = [_CS_REGION, _CS_FULL, _CS_WINDOW]
    result = _array_without(_PREFIX, existing)
    assert result == []


# --- combined add then remove (round-trip) ----------------------------------

def test_roundtrip_add_then_remove():
    """Adding then removing clipshot entries leaves only user entries."""
    user = [_USER_ENTRY]
    after_add = _array_with([_CS_REGION, _CS_FULL], user)
    after_remove = _array_without(_PREFIX, after_add)
    assert after_remove == user


def test_roundtrip_no_clobber():
    """A user's own entry is never touched during add or remove."""
    user = [_USER_ENTRY]
    after_add = _array_with([_CS_REGION], user)
    assert _USER_ENTRY in after_add
    after_remove = _array_without(_PREFIX, after_add)
    assert _USER_ENTRY in after_remove
