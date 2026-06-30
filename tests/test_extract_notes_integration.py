"""End-to-end regression test for the T1025300 scenario.

The original failure was that inline bullets 1, 2, 3 inside note 101 of
the general-notes table were emitted as bogus Distance/Flatness rows in
the main characteristics table. With the notes-block path masking the
table before detect_characteristics runs, those bullets are now in
result.notes and NOT in result.characteristics."""
from PIL import Image

from app.pipeline.detect import Detection
from app.pipeline.boxes import BoxDetection
from app.pipeline.notes_block import NotesBlockRegion
from app.pipeline.ocr.base import OcrResult


class _T1025300Backend:
    """A stub that mimics the VLM/CV pipeline on T1025300_B."""
    NOTES_TEXT = (
        "101\tCONTACT AREA NOTES\tKONTAKTBEREICH HINWEISE\n"
        "101.1\tCONTACT AREA PLANARITY 0,2mm\tKONTAKTBEREICH EBENHEIT 0,2mm\n"
        "101.2\tCONTACT AREA SURFACE QUALITY 2,5x5 Rz16\tKONTAKTBEREICH OBERFLAECHE 2,5x5 Rz16\n"
        "101.3\tPART FREE OF GREASE AND OIL\tBAUTEIL FREI VON FETT UND OEL\n"
        "102\tMEASURING POINT FOR COAT THICKNESS\tMESSPUNKT FUER SCHICHTDICKE\n"
        "103\tCOMPONENT WITHOUT SURFACE TREATMENT\tBAUTEIL OHNE OBERFLAECHENBEHANDLUNG"
    )

    def detect_regions(self, image):
        # We bypass this path entirely via monkeypatching the locator / detector,
        # but the method must exist for extract() to accept the backend.
        return []

    def read_region(self, image):
        return OcrResult(text="1,2 +0,1 -0,1", confidence=0.9)

    def read_notes_block(self, image):
        return OcrResult(text=self.NOTES_TEXT, confidence=0.9)


def test_t1025300_inline_bullets_appear_in_notes_not_characteristics(
        sample_pdf, tmp_path, monkeypatch):
    import app.pipeline.extract as extract_mod
    import app.pipeline.boxes as boxes_mod

    # No CV-detected boxes for this scenario.
    monkeypatch.setattr(boxes_mod, "detect_boxes", lambda image: [])

    # Force the locator to return the notes-block region without invoking the VLM.
    region = NotesBlockRegion(outer_box=(800, 100, 1700, 350),
                              lang_columns=[(800, 1250), (1250, 1700)])
    monkeypatch.setattr("app.pipeline.notes_block.locate_notes_block",
                        lambda image, backend: region)

    # The main detector finds three "dimensions" that, in the buggy world, would
    # have been the inline 1/2/3 bullets — but since the locator already masked
    # the block, those pixels are now white and detect_characteristics is asked
    # to find NO callouts inside it. We model that by returning only a single
    # real dimension elsewhere on the page (one outside the notes region).
    real_dim = Detection(box=(200, 600, 320, 640), kind="dimension", conf=0.9)
    monkeypatch.setattr(extract_mod, "detect_characteristics",
                        lambda image, backend, **kw: [real_dim])

    backend = _T1025300Backend()
    result = extract_mod.extract(sample_pdf, tmp_path, backend=backend)

    # The notes block was parsed and has the expected structure.
    assert result.notes is not None
    top_level = [n.pos for n in result.notes.notes if n.parent_pos is None]
    assert top_level == [101, 102, 103]
    sub_of_101 = [n.sub_index for n in result.notes.notes if n.parent_pos == 101]
    assert sub_of_101 == [1, 2, 3]

    # Crucially: the main characteristics table has ONLY the real dimension.
    # The inline 1/2/3 bullets are NOT present.
    assert len(result.characteristics) == 1
    assert result.characteristics[0].char_type == "Distance"
    # And it definitely has none of the buggy outputs from the screenshot.
    chars = result.characteristics
    bogus = [c for c in chars if c.nominal in ("16",) and c.char_type == "Distance"]
    assert bogus == []


def test_extract_populates_marks_block_alongside_notes(sample_pdf, tmp_path, monkeypatch):
    """End-to-end: when the marks locator returns a region and the backend
    transcribes it, result.marks is populated independently of result.notes."""
    import app.pipeline.extract as extract_mod
    import app.pipeline.boxes as boxes_mod
    from app.pipeline.marks_block import MarksBlockRegion
    from app.pipeline.ocr.base import OcrResult

    monkeypatch.setattr(boxes_mod, "detect_boxes", lambda image: [])

    # Notes locator returns a region (so the existing notes path still runs).
    notes_region = NotesBlockRegion(outer_box=(100, 100, 400, 300),
                                    lang_columns=[(100, 250), (250, 400)])
    monkeypatch.setattr("app.pipeline.notes_block.locate_notes_block",
                        lambda image, backend: notes_region)

    # Marks locator returns a top-right region.
    marks_region = MarksBlockRegion(outer_box=(1500, 50, 1900, 400),
                                    lang_columns=[(1500, 1700), (1700, 1900)])
    monkeypatch.setattr("app.pipeline.marks_block.locate_marks_block",
                        lambda image: marks_region)

    monkeypatch.setattr(extract_mod, "detect_characteristics",
                        lambda image, backend, **kw: [])

    class _Backend:
        # The same `read_notes_block` method is used for both notes and marks
        # transcription. The pipeline crops different regions, but our stub
        # ignores the crop and returns canned text — so we need to return
        # different text depending on which call this is. We track call count.
        def __init__(self):
            self._calls = 0
        def detect_regions(self, image): return []
        def read_region(self, image): return OcrResult(text="", confidence=0.0)
        def read_notes_block(self, image):
            self._calls += 1
            if self._calls == 1:
                return OcrResult(text="101\tNote-EN\tNote-DE", confidence=0.9)
            return OcrResult(text="111\tMark-EN\tMark-DE\n112\tM2-EN\tM2-DE",
                             confidence=0.9)

    result = extract_mod.extract(sample_pdf, tmp_path, backend=_Backend())

    assert result.notes is not None
    assert [n.pos for n in result.notes.notes] == [101]

    assert result.marks is not None
    assert [m.pos for m in result.marks.marks] == [111, 112]
    assert result.marks.marks[0].text_en == "Mark-EN"
    assert result.marks.marks[0].text_de == "Mark-DE"


def test_extract_marks_none_when_locator_returns_none(sample_pdf, tmp_path, monkeypatch):
    """When no top-right rectangle is found, result.marks is None and the
    rest of the pipeline runs unchanged."""
    import app.pipeline.extract as extract_mod
    import app.pipeline.boxes as boxes_mod
    from app.pipeline.ocr.base import OcrResult

    monkeypatch.setattr(boxes_mod, "detect_boxes", lambda image: [])
    monkeypatch.setattr("app.pipeline.notes_block.locate_notes_block",
                        lambda image, backend: None)
    monkeypatch.setattr("app.pipeline.marks_block.locate_marks_block",
                        lambda image: None)
    monkeypatch.setattr(extract_mod, "detect_characteristics",
                        lambda image, backend, **kw: [])

    class _Backend:
        def detect_regions(self, image): return []
        def read_region(self, image): return OcrResult(text="", confidence=0.0)

    result = extract_mod.extract(sample_pdf, tmp_path, backend=_Backend())
    assert result.notes is None
    assert result.marks is None
    assert result.characteristics == []
