"""Boxed callouts in a square-cornered single-cell rectangle are not ballooned;
rounded rectangles and multi-cell GD&T frames are. Synthetic images only."""
import fitz
from PIL import Image, ImageDraw

from app.models import Characteristic
from app.pipeline import boxes as bx
from app.pipeline.ballooned_pdf import _leader_segment
from app.pipeline.detect import Detection
from app.pipeline.extract import drop_plain_rectangle_detections


def _page(w=500, h=300):
    return Image.new("RGB", (w, h), "white")


def _sharp(d, box):
    d.rectangle(box, outline="black", width=3)
    d.text((box[0] + 12, box[1] + 8), "101", fill="black")


def _pill(d, box):
    d.rounded_rectangle(box, radius=(box[3] - box[1]) // 2, outline="black", width=3)
    d.text((box[0] + 20, box[1] + 8), "501", fill="black")


def test_sharp_rectangle_is_found_and_rounded_is_not():
    img = _page()
    d = ImageDraw.Draw(img)
    _sharp(d, [40, 40, 140, 84])
    _pill(d, [200, 40, 400, 84])
    rects = bx.find_plain_rectangles(img)
    assert len(rects) == 1
    assert rects[0][0] < 60 and rects[0][2] < 160


def test_multi_cell_gdt_frame_is_not_plain():
    img = _page()
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 240, 84], outline="black", width=3)
    d.line([100, 40, 100, 84], fill="black", width=3)
    d.line([170, 40, 170, 84], fill="black", width=3)
    assert bx.find_plain_rectangles(img) == []


def test_in_plain_rectangle_needs_near_coincidence():
    rect = (40, 40, 140, 84)
    assert bx.in_plain_rectangle((44, 44, 136, 80), [rect])
    # a callout merely inside a much larger frame is not a boxed one
    assert not bx.in_plain_rectangle((60, 50, 90, 70), [(0, 0, 400, 280)])
    assert not bx.in_plain_rectangle((300, 40, 360, 80), [rect])


def test_extract_drops_boxed_detections_only(monkeypatch):
    monkeypatch.delenv("SINDRI_DROP_PLAIN_RECTS", raising=False)
    img = _page()
    d = ImageDraw.Draw(img)
    _sharp(d, [40, 40, 140, 84])
    _pill(d, [200, 40, 400, 84])
    dets = [Detection(box=(40, 40, 141, 85), kind="theoretical", conf=0.8),
            Detection(box=(200, 40, 401, 85), kind="dimension", conf=0.9),
            Detection(box=(40, 150, 120, 180), kind="dimension", conf=0.9)]
    kept = drop_plain_rectangle_detections(img, dets)
    assert [k.box for k in kept] == [dets[1].box, dets[2].box]


def test_env_switch_restores_boxed(monkeypatch):
    monkeypatch.setenv("SINDRI_DROP_PLAIN_RECTS", "0")
    img = _page()
    _sharp(ImageDraw.Draw(img), [40, 40, 140, 84])
    dets = [Detection(box=(40, 40, 141, 85), kind="theoretical", conf=0.8)]
    assert drop_plain_rectangle_detections(img, dets) == dets


def test_policy_records_the_filter(monkeypatch):
    from app.pipeline.review import active_review_policy
    monkeypatch.delenv("SINDRI_DROP_PLAIN_RECTS", raising=False)
    assert active_review_policy()["detect_filters"] == ["plain_rect"]
    monkeypatch.setenv("SINDRI_DROP_PLAIN_RECTS", "0")
    assert "detect_filters" not in active_review_policy()


def test_export_share_drops_boxed_rows_and_renumbers(tmp_path):
    import export_share
    scale = 300 / 72.0
    pdf = tmp_path / "d.pdf"
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.draw_rect(fitz.Rect(30, 30, 80, 50), color=(0, 0, 0), width=1.5)   # sharp box
    page.insert_text((38, 45), "101", fontsize=10)
    page.insert_text((150, 120), "8.6", fontsize=10)
    doc.save(pdf)
    doc.close()
    boxed = Characteristic(pos=1, target_region=(30 * scale, 30 * scale, 80 * scale, 50 * scale))
    plain = Characteristic(pos=2, target_region=(148 * scale, 108 * scale, 175 * scale, 122 * scale))
    kept, dropped = export_share.refresh_rows([boxed, plain], pdf, scale)
    assert dropped == 1
    assert [c.target_region for c in kept] == [plain.target_region]
    assert kept[0].pos == 1 and kept[0].balloon_xy is not None


def test_leader_stops_outside_balloon_and_box():
    b = fitz.Point(100, 100)
    start, end = _leader_segment(b, (150, 150, 190, 170), 9.0, 2.5)
    assert abs(((start.x - 100) ** 2 + (start.y - 100) ** 2) ** 0.5 - 9.0) < 1e-6
    assert end.x <= 150 - 2.5 + 1e-6 or end.y <= 150 - 2.5 + 1e-6


def test_leader_omitted_when_balloon_touches_box():
    assert _leader_segment(fitz.Point(100, 100), (105, 105, 140, 120), 9.0, 2.5) is None


def _frame_with_stub(lw):
    """A 3-cell GD&T frame with a leader stub off the right edge, like the
    real ones: the stub stops the whole frame being found as one rectangle."""
    img = _page(700, 300)
    d = ImageDraw.Draw(img)
    d.rectangle([50, 50, 450, 130], outline="black", width=lw)
    d.line([150, 50, 150, 130], fill="black", width=lw)
    d.line([380, 50, 380, 130], fill="black", width=lw)
    d.line([450, 90, 600, 90], fill="black", width=lw)
    d.text((200, 80), "0.35", fill="black")
    return img


def test_gdt_frame_cells_are_never_dropped_at_any_line_weight():
    for lw in (3, 6):
        rects = bx.find_plain_rectangles(_frame_with_stub(lw))
        for det in ((50, 50, 451, 131),      # the whole frame
                    (50, 50, 152, 131),      # first (symbol) cell
                    (380, 50, 451, 131)):    # last (datum) cell
            assert not bx.in_plain_rectangle(det, rects), (lw, det)


def test_vertical_stack_of_boxed_numbers_is_still_plain():
    img = _page(300, 400)
    d = ImageDraw.Draw(img)
    for i, y in enumerate((40, 90, 140, 190)):
        d.rectangle([60, y, 160, y + 50], outline="black", width=3)
        d.text((95, y + 20), str(101 + i), fill="black")
    rects = bx.find_plain_rectangles(img)
    assert len(rects) >= 3
