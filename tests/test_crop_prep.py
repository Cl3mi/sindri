from PIL import Image

from app.pipeline.extract import _prep_crop, _MIN_CROP_H


def _img():
    return Image.new("RGB", (100, 100), "white")


def test_prep_crop_upscales_small_crop():
    crop = _prep_crop(_img(), (40, 40, 60, 60), 100, 100, pad=0)  # 20 px tall
    assert crop.height >= _MIN_CROP_H


def test_prep_crop_leaves_tall_crop_unscaled_with_pad_zero():
    crop = _prep_crop(_img(), (40, 40, 90, 90), 100, 100, pad=0)  # 50 px tall
    assert crop.size == (50, 50)


def test_prep_crop_pads_context_when_requested():
    crop = _prep_crop(_img(), (40, 40, 88, 88), 100, 100, pad=6)  # 48 -> 60 tall
    # padded box is 60x60 (>= min height, no upscale), i.e. 6 px added each side
    assert crop.size == (60, 60)


def test_prep_crop_clamps_padding_at_page_edge():
    crop = _prep_crop(_img(), (0, 0, 90, 90), 100, 100, pad=6)  # x0,y0 clamp to 0
    assert crop.size == (96, 96)
