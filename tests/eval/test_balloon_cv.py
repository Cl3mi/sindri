"""Rendered-page balloon detection.

For 491 of the 3,594 gold rows the balloon number is not in the PDF text layer
(outlined glyphs, raster pages), so it has to be read off the pixels. The
stamped balloons are blue and the CAD geometry is black, which is what makes
isolating them tractable.
"""
import fitz
import numpy as np
import pytest

from app.eval.balloon_cv import blue_ink_mask, detect_balloons_cv

BALLOONS = [(3, (120.0, 100.0)), (17, (300.0, 160.0)), (8, (470.0, 260.0))]


@pytest.fixture
def blue_balloon_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    # black CAD content, including digits that must NOT be read as balloons
    page.draw_line(fitz.Point(50, 350), fitz.Point(550, 350),
                   color=(0, 0, 0), width=1)
    page.insert_text(fitz.Point(60, 340), "20 +0,1", fontsize=11,
                     color=(0, 0, 0))
    page.insert_text(fitz.Point(60, 320), "45", fontsize=11, color=(0, 0, 0))
    for num, (x, y) in BALLOONS:
        r = 17.0
        page.draw_polyline([fitz.Point(x, y - r), fitz.Point(x + r, y),
                            fitz.Point(x, y + r), fitz.Point(x - r, y),
                            fitz.Point(x, y - r)], color=(0, 0, 1), width=1.5)
        page.insert_text(fitz.Point(x - 7, y + 5), str(num), fontsize=15,
                         color=(0, 0, 1))
    path = tmp_path / "blue.pdf"
    doc.save(path)
    doc.close()
    return path


def test_blue_ink_mask_keeps_blue_and_drops_black_and_white():
    img = np.zeros((3, 1, 3), dtype=np.uint8)
    img[0, 0] = (255, 0, 0)        # BGR blue
    img[1, 0] = (0, 0, 0)          # black ink
    img[2, 0] = (255, 255, 255)    # paper
    mask = blue_ink_mask(img)
    assert mask[0, 0] == 255
    assert mask[1, 0] == 0 and mask[2, 0] == 0


def test_detects_blue_balloons_from_rendered_pixels(blue_balloon_pdf):
    found = {b.number: b for b in detect_balloons_cv(blue_balloon_pdf, dpi=300)}
    assert set(found) == {3, 17, 8}
    for num, (x, y) in BALLOONS:
        bx, by = found[num].center_pt
        assert abs(bx - x) < 12 and abs(by - y) < 12, f"balloon {num} misplaced"


def test_black_dimension_text_is_not_mistaken_for_a_balloon(blue_balloon_pdf):
    numbers = {b.number for b in detect_balloons_cv(blue_balloon_pdf, dpi=300)}
    assert 20 not in numbers and 45 not in numbers


def test_expected_numbers_filter_applies(blue_balloon_pdf):
    found = detect_balloons_cv(blue_balloon_pdf, dpi=300, expect={17})
    assert [b.number for b in found] == [17]


def _flatten(src, dst, dpi=200):
    """Re-embed the page as a raster image: no text layer survives. This is the
    real failure mode — the drawings that defeat text recovery are flattened
    prints."""
    doc = fitz.open(src)
    page = doc[0]
    rect = page.rect
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
    doc.close()
    out = fitz.open()
    new_page = out.new_page(width=rect.width, height=rect.height)
    new_page.insert_image(new_page.rect, pixmap=pix)
    out.save(dst)
    out.close()
    return dst


def test_reads_balloons_off_a_page_with_no_text_layer(blue_balloon_pdf, tmp_path):
    from app.eval.balloons import recover_balloons
    flat = _flatten(blue_balloon_pdf, tmp_path / "flat.pdf")

    assert recover_balloons(flat, strategy="text") == []   # nothing to read
    found = {b.number for b in detect_balloons_cv(flat, dpi=300)}
    assert found == {3, 17, 8}


def test_reads_a_three_digit_balloon(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    x, y, r = 300.0, 200.0, 26.0            # sized to fit three digits
    page.draw_polyline([fitz.Point(x, y - r), fitz.Point(x + r, y),
                        fitz.Point(x, y + r), fitz.Point(x - r, y),
                        fitz.Point(x, y - r)], color=(0, 0, 1), width=1.5)
    page.insert_text(fitz.Point(x - 14, y + 5), "125", fontsize=15,
                     color=(0, 0, 1))
    path = tmp_path / "three.pdf"
    doc.save(path)
    doc.close()
    assert [b.number for b in detect_balloons_cv(path, dpi=300)] == [125]


def test_cv_report_measures_each_filter_stage(blue_balloon_pdf):
    """Calibration: if the detector finds nothing, this says whether the ink is
    the wrong colour, the shapes the wrong size, or the OCR the problem."""
    from app.eval.balloon_cv import cv_report
    rep = cv_report(blue_balloon_pdf, dpi=200)
    assert rep["blue_px_m40"] > 0
    assert rep["coloured_px"] > 0
    assert rep["dark_px"] > 0                 # the black dimension text
    assert rep["n_candidates"] == 3
    assert rep["n_read"] == 3


def test_cv_report_shows_when_ink_is_not_blue(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    x, y, r = 300.0, 200.0, 17.0
    page.draw_polyline([fitz.Point(x, y - r), fitz.Point(x + r, y),
                        fitz.Point(x, y + r), fitz.Point(x - r, y),
                        fitz.Point(x, y - r)], color=(0, 0, 0), width=1.5)
    page.insert_text(fitz.Point(x - 7, y + 5), "9", fontsize=15, color=(0, 0, 0))
    path = tmp_path / "black.pdf"
    doc.save(path)
    doc.close()

    from app.eval.balloon_cv import cv_report
    rep = cv_report(path, dpi=200)
    assert rep["blue_px_m40"] == 0            # colour premise fails here
    assert rep["dark_px"] > 0                 # but there IS ink to find


def test_coordinates_are_pdf_points_not_pixels(blue_balloon_pdf):
    """A 600x400pt page rendered at 300dpi is 2500x1667px; a centre reported in
    pixels would land far outside the page rectangle."""
    for b in detect_balloons_cv(blue_balloon_pdf, dpi=300):
        assert 0 <= b.center_pt[0] <= 600
        assert 0 <= b.center_pt[1] <= 400
