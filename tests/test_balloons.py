from PIL import Image, ImageDraw
from app.pipeline.balloons import label_balloons, value_region


def _balloon_image(tip):
    """White 200x200 with a blue circle at (60,100) and a filled arrow to `tip`."""
    img = Image.new("RGB", (200, 200), "white")
    d = ImageDraw.Draw(img)
    d.ellipse([42, 82, 78, 118], fill=(0, 60, 200))          # circle, centre (60,100)
    cx, cy = 60, 100
    tx, ty = tip
    # a fat triangle from the circle toward the tip
    if abs(tx - cx) >= abs(ty - cy):
        d.polygon([(cx, cy - 14), (cx, cy + 14), (tx, ty)], fill=(0, 60, 200))
    else:
        d.polygon([(cx - 14, cy), (cx + 14, cy), (tx, ty)], fill=(0, 60, 200))
    return img


def test_value_region_points_right_horizontal():
    img = _balloon_image(tip=(120, 100))
    labels = label_balloons(img)
    vr = value_region(labels, (60, 100), along=80, across=30, pad=5)
    x0, y0, x1, y1 = vr.box
    assert x0 > 60 and x1 > x0           # band is to the RIGHT of the balloon
    assert not vr.vertical


def test_value_region_points_down_vertical():
    img = _balloon_image(tip=(60, 150))
    labels = label_balloons(img)
    vr = value_region(labels, (60, 100), along=80, across=30, pad=5)
    x0, y0, x1, y1 = vr.box
    assert y0 > 100 and y1 > y0          # band is BELOW the balloon
    assert vr.vertical


def test_value_region_anchor_off_balloon_returns_none():
    img = _balloon_image(tip=(120, 100))
    labels = label_balloons(img)
    assert value_region(labels, (180, 180)) is None
