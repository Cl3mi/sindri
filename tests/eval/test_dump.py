from app.eval.dump import to_points, save_dump, load_dump
from app.eval.models import PredictionDump, RunConfig
from app.models import Characteristic, ExtractionResult


def test_to_points_inverts_render_scaling():
    scale = 300 / 72.0
    page_rect = (0.0, 0.0, 1191.0, 842.0)
    box_px = (scale * 100, scale * 50, scale * 130, scale * 60)
    assert [round(v, 6) for v in to_points(box_px, scale, page_rect)] == \
        [100.0, 50.0, 130.0, 60.0]


def test_to_points_honors_page_origin_offset():
    pt = to_points((0, 0, 72, 72), scale=1.0, page_rect=(10.0, 20.0, 500.0, 500.0))
    assert pt[0] == 10.0 and pt[1] == 20.0


def test_save_load_roundtrip(tmp_path):
    d = PredictionDump(
        doc_id="T1", config=RunConfig(model_id="stub", dpi=300),
        scale=300 / 72.0, page_rect=(0.0, 0.0, 1191.0, 842.0),
        result=ExtractionResult(characteristics=[
            Characteristic(pos=1, nominal="20", target_region=(10, 10, 40, 20)),
        ]),
    )
    path = save_dump(d, tmp_path)
    assert path.name == "T1.pred.json"
    d2 = load_dump(path)
    assert d2 == d
