"""The scan tool read a region at 0 degrees only, so a value printed at 90 or
45 degrees came back empty (operator feedback, 2026-10-28). `read_best_rotation`
tries more orientations and keeps the best-scoring read.

Synthetic text only -- never client drawings. The stub backend below stands in
for a reader that can only recognise UPRIGHT glyphs, which is what makes the
orientation search observable without a GPU: it trims the crop to its ink,
compares it with a reference render of the upright text, and answers only on a
match. So a test passes only if the candidate handed to the backend really is
upright, which is the property the real VLM depends on.
"""
from PIL import Image, ImageChops, ImageDraw, ImageFont

from app.pipeline.ocr.base import OcrResult
from app.pipeline.rotation_read import ROTATION_MARGIN, read_best_rotation

TEXT = "12.5"


def _upright(w=160, h=60):
    im = Image.new("RGB", (w, h), "white")
    ImageDraw.Draw(im).text((10, 8), TEXT, fill="black",
                            font=ImageFont.load_default(size=40))
    return im


def _ink_norm(im, size=(96, 48)):
    g = im.convert("L")
    box = ImageChops.invert(g).point(lambda v: 255 if v > 80 else 0).getbbox()
    return g.crop(box).resize(size) if box else None


_REF = _ink_norm(_upright())


class UprightOnlyBackend:
    def __init__(self):
        self.calls = 0

    def read_region(self, image):
        self.calls += 1
        probe = _ink_norm(image)
        if probe is not None:
            diff = ImageChops.difference(probe, _REF).convert("L")
            if sum(diff.getdata()) / (96 * 48) < 40:
                return OcrResult(text=TEXT, confidence=0.95)
        return OcrResult(text="", confidence=0.1)


def _printed_at(angle):
    """The upright text as the drawing would print it, rotated counter-
    clockwise by `angle` degrees on a white sheet."""
    return _upright().rotate(angle, expand=True, fillcolor="white")


def test_upright_text_is_read_in_one_call():
    b = UprightOnlyBackend()
    r = read_best_rotation(b, _printed_at(0))
    assert (r.text, r.angle) == (TEXT, 0)
    # The cheap path must stay cheap: a good first read stops the search.
    assert b.calls == 1


def test_text_printed_at_90_degrees_is_read():
    for printed in (90, -90):
        r = read_best_rotation(UprightOnlyBackend(), _printed_at(printed))
        assert r.text == TEXT, printed
        # The angle names the rotation APPLIED to the crop to read it.
        assert r.angle == -printed


def test_text_printed_at_180_degrees_is_read():
    r = read_best_rotation(UprightOnlyBackend(), _printed_at(180))
    assert (r.text, r.angle) == (TEXT, 180)


def test_text_printed_at_45_degrees_is_read():
    for printed in (45, -45):
        r = read_best_rotation(UprightOnlyBackend(), _printed_at(printed))
        assert r.text == TEXT, printed
        assert r.angle == -printed


def test_nothing_readable_returns_the_upright_empty_read():
    blank = Image.new("RGB", (120, 40), "white")
    r = read_best_rotation(UprightOnlyBackend(), blank)
    assert r.text == "" and r.angle == 0


def test_a_rotated_read_must_clearly_beat_the_upright_one():
    """Horizontal text must not flip to a rotation on a marginal score: the
    margin keeps a mediocre 0 degree read from being replaced by noise."""

    class Marginal:
        def read_region(self, image):
            # Upright is mediocre but real; every rotation is only
            # slightly better. Not enough to switch.
            marginal = image.size == (160, 60)
            return OcrResult(text="12.5", confidence=0.5 if marginal else 0.5 + ROTATION_MARGIN / 2)

    r = read_best_rotation(Marginal(), _upright())
    assert r.angle == 0


def test_a_failing_backend_is_an_empty_read_not_an_exception():
    class Broken:
        def read_region(self, image):
            raise RuntimeError("gpu fell over")

    r = read_best_rotation(Broken(), _upright())
    assert r.text == "" and r.confidence == 0.0 and r.angle == 0
