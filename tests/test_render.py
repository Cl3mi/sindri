from app.pipeline.render import render_page

def test_render_page_returns_image_and_scale(sample_pdf, tmp_path):
    result = render_page(sample_pdf, dpi=200, out_dir=tmp_path)
    assert result.png_path.exists()
    assert result.width > 1000 and result.height > 700  # landscape A2-ish
    assert abs(result.scale - 200 / 72) < 1e-6
