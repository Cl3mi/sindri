import fitz
import pytest
from PIL import Image

from app.pipeline.render import MAX_RENDER_PIXELS, _budget_scale, render_page


def test_render_page_returns_image_and_scale(sample_pdf, tmp_path):
    result = render_page(sample_pdf, dpi=200, out_dir=tmp_path)
    assert result.png_path.exists()
    assert result.width > 1000 and result.height > 700  # landscape A2-ish
    assert abs(result.scale - 200 / 72) < 1e-6


def _page_pdf(path, width_pt, height_pt):
    doc = fitz.open()
    doc.new_page(width=width_pt, height=height_pt)
    doc.save(path)
    doc.close()
    return path


def test_render_page_clamps_scale_to_the_pixel_budget(tmp_path):
    """A large-format drawing must render inside the budget instead of blowing
    up PIL — and the reported scale must describe the pixels actually produced,
    not the dpi that was asked for."""
    pdf = _page_pdf(tmp_path / "big.pdf", 600, 400)
    budget = 1_000_000                       # 300 dpi would need ~4.2 M pixels

    result = render_page(pdf, dpi=300, out_dir=tmp_path, max_pixels=budget)

    assert result.width * result.height <= budget
    assert result.scale < 300 / 72
    # the reported scale is the real one: it maps page points to rendered pixels
    assert result.width == pytest.approx(600 * result.scale, abs=1)
    assert result.height == pytest.approx(400 * result.scale, abs=1)
    # and it spends the budget rather than shrinking far below it
    assert result.width * result.height > budget * 0.8


def test_render_page_keeps_requested_dpi_when_it_fits(tmp_path):
    pdf = _page_pdf(tmp_path / "small.pdf", 600, 400)
    result = render_page(pdf, dpi=300, out_dir=tmp_path, max_pixels=1_000_000_000)
    assert result.scale == pytest.approx(300 / 72)


def test_render_page_logs_the_effective_dpi_when_it_clamps(tmp_path, capsys):
    pdf = _page_pdf(tmp_path / "big.pdf", 600, 400)
    render_page(pdf, dpi=300, out_dir=tmp_path, max_pixels=1_000_000)
    err = capsys.readouterr().err
    assert "300" in err and "dpi" in err.lower()


def test_pixel_budget_admits_a_full_300_dpi_render_of_the_clamped_sheets(tmp_path):
    """The Rung-0 baseline clamped two sheets to 225 dpi, and they carried 76 of
    the 118 undetected misses on clamped documents. 4050x2023 pt is that sheet's
    shape: it needs 142 MP at 300 dpi, so an 80 MP budget forced it to 225.

    The second assertion pins what the old budget did, so this stays a
    regression test for the specific drawing rather than a bare constant."""
    full = 300 / 72.0
    assert _budget_scale(4050, 2023, full, MAX_RENDER_PIXELS) == pytest.approx(full)
    # What the old budget did to this sheet. Banded, not exact: the 0.99 shrink
    # step overshoots by up to 1% of scale, so the landing dpi is not a clean
    # function of the budget. The corpus showed 225; this lands in that band.
    old_dpi = _budget_scale(4050, 2023, full, 80_000_000) * 72
    assert 215 < old_dpi < 230, f"old budget clamped this sheet to {old_dpi:.0f} dpi"


def test_pixel_budget_stays_below_pils_decompression_bomb_error(tmp_path):
    """The budget may exceed PIL's *warning* threshold now -- that is the point,
    142 MP sits above it -- but it must stay under the ERROR threshold (2x), or
    Image.open would raise on a render the pipeline deliberately produced."""
    assert MAX_RENDER_PIXELS < 2 * 89_478_485      # PIL's stock error ceiling


def test_render_lifts_pils_own_limit_above_the_budget_so_it_does_not_warn():
    """Raising the budget past 89.5 MP means PIL warns on every oversized sheet
    unless its limit is lifted too. Lift it to just above our own budget rather
    than disabling it (None), so a genuinely absurd input is still caught."""
    assert Image.MAX_IMAGE_PIXELS is not None, "the bomb guard must stay armed"
    assert Image.MAX_IMAGE_PIXELS > MAX_RENDER_PIXELS
