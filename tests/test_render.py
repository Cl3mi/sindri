import fitz
import pytest
from PIL import Image

from app.pipeline.render import MAX_RENDER_PIXELS, render_page


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


def test_pixel_budget_sits_below_pils_decompression_bomb_warning(tmp_path):
    """80 MP: under PIL's *warning* threshold, so no global PIL state has to be
    touched and no oversized drawing produces a warning or an error."""
    assert MAX_RENDER_PIXELS == 80_000_000
    assert MAX_RENDER_PIXELS < Image.MAX_IMAGE_PIXELS
