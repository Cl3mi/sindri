from app.pipeline.notes_block import parse_notes_block


def test_parses_top_level_bilingual_row():
    raw = "101\tCONTACT AREA NOTES\tKONTAKTBEREICH HINWEISE"
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    assert nb.region == (0, 0, 100, 100)
    assert len(nb.notes) == 1
    n = nb.notes[0]
    assert n.pos == 101 and n.parent_pos is None and n.sub_index is None
    assert n.text_en == "CONTACT AREA NOTES"
    assert n.text_de == "KONTAKTBEREICH HINWEISE"
    assert n.raw_text == raw


def test_parses_sub_bullet_links_parent():
    raw = (
        "101\tCONTACT AREA NOTES\tKONTAKTBEREICH HINWEISE\n"
        "101.1\tPLANARITY 0,2mm\tEBENHEIT 0,2mm"
    )
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    assert len(nb.notes) == 2
    sub = nb.notes[1]
    assert sub.pos == 1
    assert sub.parent_pos == 101
    assert sub.sub_index == 1
    assert sub.text_en == "PLANARITY 0,2mm"
    assert sub.text_de == "EBENHEIT 0,2mm"


def test_parses_single_language_row_when_no_tab_after_en():
    raw = "102\tPART FREE OF GREASE AND OIL"
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    assert len(nb.notes) == 1
    n = nb.notes[0]
    assert n.text_en == "PART FREE OF GREASE AND OIL"
    assert n.text_de == ""


def test_drops_malformed_lines_silently():
    raw = (
        "this is not a note row\n"
        "101\tA\tB\n"
        "\n"
        "garbage 999\n"
    )
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    positions = [n.pos for n in nb.notes]
    assert positions == [101]


def test_parses_multiple_top_level_and_sub_bullets():
    raw = (
        "101\tA-en\tA-de\n"
        "101.1\tA1-en\tA1-de\n"
        "101.2\tA2-en\tA2-de\n"
        "102\tB-en\tB-de\n"
    )
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    flat = [(n.pos, n.parent_pos, n.sub_index) for n in nb.notes]
    assert flat == [(101, None, None), (1, 101, 1), (2, 101, 2), (102, None, None)]


def test_three_digit_pos_outside_10x_range_still_accepted():
    raw = "199\tnote text en\tnote text de"
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    assert nb.notes[0].pos == 199


from app.models import Note
from app.pipeline.notes_block import review_flags_note


def _note(**kw):
    base = dict(pos=101, text_en="A", text_de="B", raw_text="101\tA\tB")
    base.update(kw)
    return Note(**base)


def test_clean_top_level_note_not_flagged():
    flagged, reasons = review_flags_note(
        _note(), two_columns=True, known_parents=set())
    assert flagged is False
    assert reasons == []


def test_empty_raw_text_is_flagged():
    flagged, reasons = review_flags_note(
        _note(raw_text="", text_en="", text_de=""),
        two_columns=True, known_parents=set())
    assert flagged is True
    assert reasons == ["empty read"]


def test_missing_translation_when_two_columns_expected():
    _, reasons = review_flags_note(
        _note(text_de=""), two_columns=True, known_parents=set())
    assert reasons == ["missing translation"]


def test_missing_translation_not_reported_for_single_column_block():
    _, reasons = review_flags_note(
        _note(text_de=""), two_columns=False, known_parents=set())
    assert reasons == []


def test_orphan_sub_bullet_when_parent_not_in_block():
    sub = _note(pos=1, parent_pos=999, sub_index=1, raw_text="999.1\tA\tB")
    _, reasons = review_flags_note(sub, two_columns=True, known_parents={101})
    assert "orphan sub-bullet" in reasons


def test_sub_bullet_with_known_parent_not_flagged_for_orphan():
    sub = _note(pos=1, parent_pos=101, sub_index=1, raw_text="101.1\tA\tB")
    _, reasons = review_flags_note(sub, two_columns=True, known_parents={101})
    assert reasons == []


def test_empty_read_suppresses_missing_translation():
    _, reasons = review_flags_note(
        _note(raw_text="", text_en="", text_de=""),
        two_columns=True, known_parents=set())
    assert reasons == ["empty read"]


from PIL import Image
from app.pipeline.notes_block import mask_region, NotesBlockRegion


def test_mask_region_fills_with_white_inside_box():
    img = Image.new("RGB", (100, 100), "black")
    region = NotesBlockRegion(outer_box=(20, 30, 60, 70), lang_columns=[(20, 60)])
    out = mask_region(img, region)
    # inside the box is white
    assert out.getpixel((30, 40)) == (255, 255, 255)
    # outside the box is unchanged
    assert out.getpixel((10, 10)) == (0, 0, 0)
    # original image is untouched (copy semantics)
    assert img.getpixel((30, 40)) == (0, 0, 0)


def test_mask_region_box_with_zero_area_no_op():
    img = Image.new("RGB", (50, 50), "black")
    region = NotesBlockRegion(outer_box=(10, 10, 10, 10), lang_columns=[(10, 10)])
    out = mask_region(img, region)
    # still all black
    assert out.getpixel((10, 10)) == (0, 0, 0)
    assert out.getpixel((25, 25)) == (0, 0, 0)


from app.pipeline.detect import Detection
from app.pipeline.boxes import BoxDetection
from app.pipeline.notes_block import locate_notes_block


class _StubBackendNotes:
    """Returns the same note detections for every tile (the locator's tile-grid
    pass will pick them up at offset (0,0))."""
    def __init__(self, detections):
        self._dets = detections

    def detect_regions(self, image):
        return list(self._dets)


def _white_image(w=400, h=400):
    return Image.new("RGB", (w, h), "white")


def test_locate_returns_none_when_no_note_detections(monkeypatch):
    backend = _StubBackendNotes(detections=[])
    monkeypatch.setattr("app.pipeline.notes_block.detect_boxes", lambda image: [])
    region = locate_notes_block(_white_image(), backend)
    assert region is None


def test_locate_clusters_adjacent_note_detections(monkeypatch):
    # Three note detections stacked vertically inside the same column.
    dets = [
        Detection(box=(50, 20, 200, 40), kind="note", conf=0.9),
        Detection(box=(50, 50, 200, 70), kind="note", conf=0.9),
        Detection(box=(50, 80, 200, 100), kind="note", conf=0.9),
    ]
    backend = _StubBackendNotes(dets)
    monkeypatch.setattr("app.pipeline.notes_block.detect_boxes", lambda image: [])
    region = locate_notes_block(_white_image(), backend)
    assert region is not None
    # outer_box covers the union of the three, padded by 8
    assert region.outer_box[0] <= 50 and region.outer_box[1] <= 20
    assert region.outer_box[2] >= 200 and region.outer_box[3] >= 100


def test_locate_snaps_to_overlapping_cv_rectangle(monkeypatch):
    dets = [Detection(box=(60, 60, 200, 90), kind="note", conf=0.9)]
    cv = BoxDetection(outer_box=(50, 50, 220, 110), inner_box=(54, 54, 216, 106),
                      cells=2, subtype="theoretical", conf=0.8)
    backend = _StubBackendNotes(dets)
    monkeypatch.setattr("app.pipeline.notes_block.detect_boxes", lambda image: [cv])
    region = locate_notes_block(_white_image(), backend)
    # Snapped to the CV rectangle (which is larger and overlaps).
    assert region.outer_box == (50, 50, 220, 110)


def test_locate_returns_none_when_detector_raises(monkeypatch):
    class Boom:
        def detect_regions(self, image):
            raise RuntimeError("kaboom")
    monkeypatch.setattr("app.pipeline.notes_block.detect_boxes", lambda image: [])
    assert locate_notes_block(_white_image(), Boom()) is None


from app.pipeline.notes_block import read_notes_block


class _StubBackendRead:
    def __init__(self, text):
        self._text = text

    def read_region(self, image):
        from app.pipeline.ocr.base import OcrResult
        return OcrResult(text=self._text, confidence=0.9)


def test_read_notes_block_returns_backend_text():
    backend = _StubBackendRead("101\tA-en\tA-de")
    region = NotesBlockRegion(outer_box=(0, 0, 100, 100), lang_columns=[(0, 50), (50, 100)])
    text = read_notes_block(Image.new("RGB", (200, 200), "white"), region, backend)
    assert text == "101\tA-en\tA-de"


def test_read_notes_block_uses_notes_method_when_available():
    class WithNotesMethod:
        def read_notes_block(self, image):
            from app.pipeline.ocr.base import OcrResult
            return OcrResult(text="from-notes-method", confidence=0.9)
        def read_region(self, image):
            from app.pipeline.ocr.base import OcrResult
            return OcrResult(text="from-generic-read", confidence=0.9)

    region = NotesBlockRegion(outer_box=(0, 0, 100, 100), lang_columns=[(0, 100)])
    text = read_notes_block(Image.new("RGB", (200, 200), "white"),
                            region, WithNotesMethod())
    assert text == "from-notes-method"


def test_read_notes_block_returns_empty_string_when_backend_raises():
    class Boom:
        def read_region(self, image):
            raise RuntimeError("kaboom")
    region = NotesBlockRegion(outer_box=(0, 0, 100, 100), lang_columns=[(0, 100)])
    text = read_notes_block(Image.new("RGB", (200, 200), "white"), region, Boom())
    assert text == ""
