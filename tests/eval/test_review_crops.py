"""Crops of a drawing around one review row -- geometry first, pixels second.

The review app shows the operator the callout instead of making them open a PDF
and hunt for a balloon. The region must contain both the pipeline's box and
gold's balloon position with enough context to read the frame, never be cut
off at the page edge, and rendering must never modify the drawing on disk.
"""
import fitz
import pytest

from app.eval.review_crops import (CropError, crop_rect, page_rect,
                                   placeholder_png, render_crop)

PAGE = (0.0, 0.0, 1191.0, 842.0)
PNG = b"\x89PNG"


def test_the_region_covers_box_and_gold_point_with_padding():
    r = crop_rect(PAGE, (100, 100, 200, 130), (300, 110))
    assert r[0] <= 100 - 40 and r[2] >= 300 + 5 + 40
    assert r[1] <= 100 - 40 and r[3] >= 130 + 40


def test_a_tiny_region_grows_to_the_minimum_around_its_centre():
    """A lone gold point would give a crop too small to read the frame."""
    x0, y0, x1, y1 = crop_rect(PAGE, None, (600, 400))
    assert x1 - x0 == pytest.approx(160) and y1 - y0 == pytest.approx(100)
    assert (x0 + x1) / 2 == pytest.approx(600)


def test_a_region_at_the_page_edge_is_shifted_inside_not_cut():
    x0, y0, x1, y1 = crop_rect(PAGE, None, (2, 2))
    assert (x0, y0) == (0, 0) and x1 - x0 == pytest.approx(160)


def test_nothing_to_show_means_no_region():
    assert crop_rect(PAGE, None, None) is None


def _pdf(tmp_path):
    path = tmp_path / "d.pdf"
    doc = fitz.open()
    page = doc.new_page(width=PAGE[2], height=PAGE[3])
    page.insert_text((120, 120), "0,05 A", fontsize=12)
    doc.save(path)
    doc.close()
    return path


def test_a_crop_renders_as_png_and_leaves_the_drawing_untouched(tmp_path):
    """Overlays are drawn on an in-memory page that is never saved."""
    path = _pdf(tmp_path)
    before = path.read_bytes()
    png = render_crop(path, (60, 60, 260, 200), (100, 100, 200, 130), (150, 115))
    assert png.startswith(PNG)
    assert path.read_bytes() == before


def test_page_rect_reads_the_first_page(tmp_path):
    assert page_rect(_pdf(tmp_path)) == pytest.approx(PAGE)
    assert page_rect(tmp_path / "missing.pdf") is None


def test_a_missing_drawing_is_a_crop_error_not_a_crash(tmp_path):
    with pytest.raises(CropError):
        render_crop(tmp_path / "missing.pdf", (0, 0, 100, 100))


def test_the_placeholder_is_a_png():
    assert placeholder_png("drawing not available").startswith(PNG)


# --- a stamped sheet of a different size --------------------------------------
#
# Ingest maps each stamped balloon into the ORIGINAL's space with a per-axis
# scale between the two page rects (ingest._to_target). On most dev documents
# the sheets differ in extent, so the stamped crop must invert that same map --
# cutting the stamped sheet at original-space coordinates shows an unrelated
# part of the drawing (reported by the operator on 6 of 8 rows, 2026-09-25).

from app.eval.review_crops import map_rect  # noqa: E402


def test_map_rect_inverts_ingests_per_axis_scale():
    orig, stamped = (0, 0, 842, 595), (0, 0, 1684, 1190)
    assert map_rect((700, 500, 710, 510), orig, stamped) == pytest.approx(
        (1400, 1000, 1420, 1020))


def test_map_rect_honours_page_origins_and_unequal_axes():
    orig, stamped = (10, 20, 110, 220), (0, 0, 300, 100)
    # x: (60-10)*3 = 150; y: (120-20)*0.5 = 50
    assert map_rect((60, 120, 60, 120), orig, stamped) == pytest.approx(
        (150, 50, 150, 50))


def test_map_rect_is_the_identity_on_equal_sheets():
    page = (0, 0, 1191, 842)
    assert map_rect((1, 2, 3, 4), page, page) == pytest.approx((1, 2, 3, 4))
