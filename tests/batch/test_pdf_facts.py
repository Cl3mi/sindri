"""Staging's facts about a PDF (batch design §2, decision 26): page count,
whether sheet 1 would be render-clamped, and whether it can be read at all."""
import pytest

from app.batch.pdf_facts import EXTRACT_DPI, PdfFacts, pdf_facts
from app.pipeline.render import effective_dpi
from tests.batch.pdfs import encrypted_pdf_bytes, pdf_bytes


def write(tmp_path, data):
    p = tmp_path / "input.pdf"
    p.write_bytes(data)
    return p


def test_a_one_page_sheet_is_in_scope(tmp_path):
    assert pdf_facts(write(tmp_path, pdf_bytes())) == PdfFacts(readable=True, pages=1, oversized=False)


def test_pages_are_counted(tmp_path):
    assert pdf_facts(write(tmp_path, pdf_bytes(pages=3))).pages == 3


def test_a_sheet_the_renderer_would_clamp_is_oversized(tmp_path):
    facts = pdf_facts(write(tmp_path, pdf_bytes(width=7000, height=5000)))
    assert facts.readable and facts.oversized


@pytest.mark.parametrize("w,h", [(595, 842), (3370, 2384), (7000, 5000)])   # A4, A0, 2.5 x 1.7 m
def test_oversized_is_the_probe_rule_asked_of_the_renderer(tmp_path, w, h):
    """The harness calls a sheet render-clamped when
    effective_dpi(w, h, 300) < 300 - 1.0 (app/eval/balloons.py). Staging must
    answer the same for the same sheet, or the badge and the scope policy's
    published numbers would disagree about one drawing."""
    expected = effective_dpi(w, h, 300) < 299.0
    assert pdf_facts(write(tmp_path, pdf_bytes(width=w, height=h))).oversized == expected


@pytest.mark.parametrize("data", [b"", b"not a pdf at all", encrypted_pdf_bytes()],
                         ids=["empty", "garbage", "password"])
def test_an_unreadable_file_is_unreadable_never_an_error(tmp_path, data):
    assert pdf_facts(write(tmp_path, data)) == PdfFacts(readable=False)


def test_extraction_runs_at_the_dpi_oversized_is_measured_at(monkeypatch, tmp_path):
    """`oversized` means "read below the resolution extraction asks for";
    if the two dpi values ever drifted apart the badge would lie."""
    import app.main as main
    seen = {}

    def fake_extract(pdf_path, work_dir, dpi, backend, progress):
        seen["dpi"] = dpi
    monkeypatch.setattr(main, "extract", fake_extract)
    main._extract(tmp_path / "x", tmp_path, lambda *a, **k: None)
    assert seen["dpi"] == EXTRACT_DPI == 300
