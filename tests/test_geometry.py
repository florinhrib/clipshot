from clipshot.geometry import Rect, handle_at


def test_from_points_normalises():
    r = Rect.from_points(100, 80, 20, 10)
    assert (r.x, r.y, r.w, r.h) == (20, 10, 80, 70)
    assert r.x1 == 100 and r.y1 == 80


def test_clamp_to_bounds():
    bounds = Rect(0, 0, 1920, 1080)
    r = Rect(-50, -50, 200, 200).clamp(bounds)
    assert r.x == 0 and r.y == 0
    assert r.x1 <= 1920 and r.y1 <= 1080
    r2 = Rect(1900, 1060, 200, 200).clamp(bounds)
    assert r2.x1 == 1920 and r2.y1 == 1080


def test_translate_and_grow():
    r = Rect(10, 10, 100, 100)
    assert r.translated(5, -5) == Rect(15, 5, 100, 100)
    grown = r.grown("se", 20, 30)
    assert grown.w == 120 and grown.h == 130
    grown_nw = r.grown("nw", -10, -10)
    assert grown_nw.x == 0 and grown_nw.y == 0 and grown_nw.w == 110


def test_aspect_lock():
    r = Rect(0, 0, 200, 50)
    locked = r.with_aspect(1.0)  # square
    assert locked.w == locked.h


def test_handle_hit_testing():
    r = Rect(100, 100, 200, 200)  # corners 100,100 .. 300,300
    assert handle_at(r, 100, 100) == "nw"
    assert handle_at(r, 300, 300) == "se"
    assert handle_at(r, 200, 100) == "n"
    assert handle_at(r, 200, 200) == "inside"
    assert handle_at(r, 1000, 1000) is None


def test_empty_detection():
    assert Rect(0, 0, 0, 50).is_empty()
    assert not Rect(0, 0, 5, 5).is_empty()
