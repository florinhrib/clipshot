"""Headless flatten test for the annotation editor.

Exercises the module-level export helpers (render_to_surface / surface_to_pil /
flatten) which take plain PIL images + annotation objects, so the whole draw +
flatten path runs without constructing the GTK window or touching a display.
"""
from PIL import Image

from clipshot.annotate import tools
from clipshot.annotate.editor import flatten, render_to_surface, surface_to_pil


def _base(w=120, h=90, color=(255, 255, 255, 255)):
    return Image.new("RGBA", (w, h), color)


def test_flatten_size_and_nonempty():
    base = _base()
    objects = [
        tools.RectTool(x0=10, y0=10, x1=80, y1=60, color=(1, 0, 0, 1), width=4),
        tools.ArrowTool(x0=5, y0=80, x1=110, y1=20, color=(0, 0, 1, 1), width=5),
    ]
    out = flatten(base, objects)

    assert isinstance(out, Image.Image)
    assert out.size == base.size
    assert out.mode == "RGBA"

    # the white canvas now has red/blue pixels painted on it -> more than one colour
    colors = out.getcolors(maxcolors=100000)
    assert colors is not None
    assert len(colors) > 1, "flattened image should contain annotation pixels"

    # red rectangle stroke should land somewhere near its top edge
    found_red = any(
        r > 150 and g < 100 and b < 100
        for _count, (r, g, b, _a) in colors
    )
    assert found_red, "expected red rectangle pixels in the flattened output"


def test_surface_roundtrip_dimensions():
    base = _base(40, 30, color=(10, 20, 30, 255))
    surface = render_to_surface(base, [])
    assert surface.get_width() == 40 and surface.get_height() == 30
    out = surface_to_pil(surface)
    assert out.size == (40, 30)
    # a blank-annotation flatten should preserve the base colour
    px = out.getpixel((5, 5))
    assert px[0:3] == (10, 20, 30)


def test_ellipse_and_text_render():
    base = _base(120, 90)
    objects = [
        tools.EllipseTool(x0=10, y0=10, x1=100, y1=70, color=(0, 1, 0, 1), width=4),
        tools.TextTool(x0=12, y0=12, color=(0, 0, 1, 1), text="hi", font_size=20),
    ]
    out = flatten(base, objects)
    assert out.size == base.size
    colors = out.getcolors(maxcolors=100000)
    assert colors is not None and len(colors) > 1


def test_pixelate_mutates_region():
    # gradient so pixelation visibly changes the region
    base = Image.new("RGBA", (40, 40), (0, 0, 0, 255))
    for x in range(40):
        for y in range(40):
            base.putpixel((x, y), (x * 6 % 256, y * 6 % 256, 0, 255))
    before = base.copy()
    obj = tools.PixelateTool(x0=5, y0=5, x1=35, y1=35, block=8)
    out = flatten(base, [obj])
    assert out.size == (40, 40)
    # something inside the region should differ from the original
    assert any(
        out.getpixel((x, y))[:3] != before.getpixel((x, y))[:3]
        for x in range(6, 34, 4)
        for y in range(6, 34, 4)
    )
