"""campaign_build (campaign design §2; plan C1 decision 4). Synthetic dump
and the repo's own sample PDF only -- the real tool is the operator's."""
import json

import pytest

from app.eval.dump import save_dump
from app.eval.models import PredictionDump, RunConfig
from app.models import Characteristic, ExtractionResult

import campaign_build


def _dump(tmp_path, sample_pdf, extra_rows=()):
    rows = [Characteristic(pos=1, id="r1", raw_text="20 +0,1 -0,1", nominal="20",
                           kind="dimension", target_region=(40, 40, 120, 70),
                           balloon_xy=(10, 10), confidence=0.995),
            *extra_rows]
    dump = PredictionDump(doc_id="DOC-1", config=RunConfig(), scale=300 / 72.0,
                          page_rect=(0, 0, 595, 842),
                          result=ExtractionResult(characteristics=rows))
    return save_dump(dump, tmp_path / "runs" / "r5-control")


def test_an_entry_holds_what_the_loader_needs(tmp_path, sample_pdf):
    dump_path = _dump(tmp_path, sample_pdf)
    out = tmp_path / "bundle"
    entry = campaign_build.build_entry(dump_path, sample_pdf, out, campaign="pilot-1",
                                       group="great", run="r5-control", doc_hash="aa")
    assert {p.name for p in entry.iterdir()} == {
        "input.pdf", "page.png", "result.json", "campaign.json"}
    result = json.loads((entry / "result.json").read_text())
    assert set(result) == {"rows", "notes", "marks", "title_block"}
    assert [r["id"] for r in result["rows"]] == ["r1"]
    assert result["rows"][0]["balloon_xy"] is not None   # re-placed for today
    facts = json.loads((entry / "campaign.json").read_text())
    assert facts == {"campaign": "pilot-1", "group": "great", "source_run": "r5-control",
                     "doc_hash": "aa", "file_name": "DOC-1.pdf"}


def test_a_row_todays_policy_drops_rides_along_as_a_suggestion(tmp_path, sample_pdf):
    """Decision 4: the client grades what we ship TODAY, tray included. A row
    an old run delivered but today's stage-2 drop (confidence below 0.99)
    removes must reach the bundle as a suggestion -- pos 0, suggested -- not
    vanish and not ship as a confirmed balloon."""
    low = Characteristic(pos=2, id="r2", raw_text="35 +0,2 -0,2", nominal="35",
                         kind="dimension", target_region=(300, 400, 380, 430),
                         balloon_xy=(290, 390), confidence=0.9)
    dump_path = _dump(tmp_path, sample_pdf, extra_rows=[low])
    entry = campaign_build.build_entry(dump_path, sample_pdf, tmp_path / "bundle",
                                       campaign="pilot-1", group="problem",
                                       run="r3-trainpredict", doc_hash="bb")
    rows = {r["id"]: r for r in json.loads((entry / "result.json").read_text())["rows"]}
    assert rows["r1"]["suggested"] is False and rows["r1"]["pos"] == 1
    assert rows["r2"]["suggested"] is True and rows["r2"]["pos"] == 0


def test_a_render_at_another_scale_refuses_the_entry(tmp_path, sample_pdf, monkeypatch):
    """The review screen draws the rows' pixel boxes on page.png. If the page
    no longer renders at the dump's scale (the pixel budget clamped it), every
    box would sit in the wrong place -- refresh_rows only warns and leaves the
    rows as stored, so the build must refuse rather than ship misaligned
    boxes, and must leave no half-written entry behind."""
    real = campaign_build.render_page

    def clamped(pdf_path, dpi, out_dir, **kw):
        return real(pdf_path, dpi=dpi // 2, out_dir=out_dir, **kw)
    monkeypatch.setattr(campaign_build, "render_page", clamped)
    out = tmp_path / "bundle"
    with pytest.raises(campaign_build.ScaleMismatch):
        campaign_build.build_entry(_dump(tmp_path, sample_pdf), sample_pdf, out,
                                   campaign="pilot-1", group="great",
                                   run="r5-control", doc_hash="cc")
    assert not (out / "cc").exists()
