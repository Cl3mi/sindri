import os
import pytest

gpu_only = pytest.mark.skipif(
    os.getenv("RUN_GPU_TESTS") != "1",
    reason="set RUN_GPU_TESTS=1 on a GPU host with the VLM model available")


@gpu_only
def test_vlm_detects_callouts_on_real_drawing(sample_pdf, tmp_path):
    from PIL import Image
    from app.pipeline.render import render_page
    from app.pipeline.detect import detect_characteristics
    from app.pipeline.ocr.vlm_backend import VLMBackend

    render = render_page(sample_pdf, dpi=300, out_dir=tmp_path)
    image = Image.open(render.png_path).convert("RGB")
    dets = detect_characteristics(image, VLMBackend())
    assert len(dets) >= 10
