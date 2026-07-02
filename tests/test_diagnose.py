from PIL import Image, ImageDraw

from app.models import Characteristic, ExtractionResult, Mark, MarkBlock, Note, NoteBlock
from app.pipeline.diagnose import build_cv_report, summarize_result, capture_raw_reads
from app.pipeline.ocr.base import OcrResult


def _page_with_legend_and_title(w=1000, h=900):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    # top-right legend (3 rows, mark# + description columns, ink each row)
    left, top, right, div, bottom = 600, 6, w - 6, 680, 330
    d.rectangle((6, 6, w - 6, h - 6), outline="black", width=3)
    d.line((div, top, div, bottom), fill="black", width=3)
    d.line((left, top, left, bottom), fill="black", width=3)
    d.line((left, bottom, right, bottom), fill="black", width=3)
    for y in (90, 210):
        d.line((left, y, right, y), fill="black", width=3)
    for cy in (48, 150, 270):
        d.rectangle((615, cy - 8, 670, cy + 8), fill="black")
        d.rectangle((700, cy - 6, right - 40, cy + 6), fill="black")
    # bottom-right title block grid
    d.rectangle((600, 620, 980, 860), outline="black", width=3)
    d.line((790, 620, 790, 860), fill="black", width=3)
    d.line((600, 740, 980, 740), fill="black", width=3)
    d.rectangle((630, 650, 720, 700), fill="black")
    return img


def test_build_cv_report_locates_marks_and_title():
    report = build_cv_report(_page_with_legend_and_title())
    assert report["marks"]["located"] is True
    # spans the multi-row legend, not just one cell
    assert report["marks"]["height_frac"] >= 0.25
    assert report["title_block"]["located"] is True


def test_build_cv_report_blank_page():
    report = build_cv_report(Image.new("RGB", (1000, 900), "white"))
    assert report["marks"]["located"] is False
    assert report["title_block"]["located"] is False


class _FakeVLM:
    """Backend exposing only read_notes_block (used by read_marks_block)."""
    def __init__(self, notes_text):
        self._t = notes_text
    def read_notes_block(self, image):
        return OcrResult(text=self._t, confidence=0.9)


def test_capture_raw_reads_reports_marks_transcription_and_tab_flag():
    img = _page_with_legend_and_title()          # marks region is locatable
    backend = _FakeVLM("101 CONTACT AREA\n102 PART FREE")   # spaces, no tabs
    raw = capture_raw_reads(img, backend)
    assert raw["marks"]["has_tab"] is False
    assert "101" in raw["marks"]["preview"]
    assert raw["marks"]["chars"] > 0


def _char(pos, box):
    return Characteristic(pos=pos, kind="dimension", char_type="Distance",
                          nominal="3.2", target_region=box)


def test_summarize_flags_overlapping_duplicates():
    chars = [
        _char(1, (0, 0, 100, 100)),
        _char(2, (3, 3, 103, 103)),        # ~duplicate of pos 1
        _char(3, (500, 500, 560, 560)),    # distinct
    ]
    summary = summarize_result(ExtractionResult(characteristics=chars))
    assert summary["characteristics"] == 3
    assert len(summary["potential_duplicates"]) == 1


def test_summarize_counts_blocks():
    result = ExtractionResult(
        characteristics=[_char(1, (0, 0, 10, 10))],
        marks=MarkBlock(region=(0, 0, 1, 1), marks=[Mark(pos=101), Mark(pos=102)]),
        notes=NoteBlock(region=(0, 0, 1, 1), notes=[Note(pos=101)]),
    )
    summary = summarize_result(result)
    assert summary["marks"] == 2
    assert summary["notes"] == 1
    assert summary["by_kind"] == {"dimension": 1}
