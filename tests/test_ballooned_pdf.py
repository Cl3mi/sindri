import fitz
from app.models import Characteristic
from app.pipeline.ballooned_pdf import render_ballooned_pdf


def _row(pos, box, balloon):
    c = Characteristic(pos=pos)
    c.target_region = box
    c.balloon_xy = balloon
    return c


def test_render_ballooned_pdf_adds_drawings_and_leaves_source_untouched(sample_pdf, tmp_path):
    before = fitz.open(sample_pdf)
    n_before = len(before[0].get_drawings())
    src_bytes_before = sample_pdf.read_bytes()
    before.close()

    rows = [_row(1, (300, 300, 380, 330), (200, 200)),
            _row(2, (600, 400, 680, 430), (500, 320))]
    out = tmp_path / "ballooned.pdf"
    render_ballooned_pdf(sample_pdf, rows, dpi=300, out_path=out)

    assert out.exists()
    after = fitz.open(out)
    assert len(after[0].get_drawings()) > n_before
    text = after[0].get_text()
    assert "1" in text and "2" in text
    after.close()

    assert sample_pdf.read_bytes() == src_bytes_before


def test_render_ballooned_pdf_skips_rows_without_geometry(sample_pdf, tmp_path):
    rows = [Characteristic(pos=1)]
    out = tmp_path / "b.pdf"
    render_ballooned_pdf(sample_pdf, rows, dpi=300, out_path=out)
    assert out.exists()
