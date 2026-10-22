"""campaign_build (campaign design §2; plan C1 decision 4). Synthetic dump
and the repo's own sample PDF only -- the real tool is the operator's."""
import json
import shutil

import pytest
from PIL import Image

from app.eval.anon import Anonymizer
from app.eval.dump import save_dump
from app.eval.models import PredictionDump, RunConfig
from app.models import Characteristic, ExtractionResult

import campaign_build
import export_share


def _dump(tmp_path, sample_pdf, extra_rows=(), scale=300 / 72.0, doc_id="DOC-1",
          run_dir=None):
    rows = [Characteristic(pos=1, id="r1", raw_text="20 +0,1 -0,1", nominal="20",
                           kind="dimension", target_region=(40, 40, 120, 70),
                           balloon_xy=(10, 10), confidence=0.995),
            *extra_rows]
    dump = PredictionDump(doc_id=doc_id, config=RunConfig(), scale=scale,
                          page_rect=(0, 0, 595, 842),
                          result=ExtractionResult(characteristics=rows))
    return save_dump(dump, run_dir or tmp_path / "runs" / "r5-control")


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
    assert [p.name for p in out.iterdir()] == ["aa"]       # no temp dir left over


def test_a_row_todays_policy_drops_rides_along_as_a_suggestion(tmp_path, sample_pdf):
    """Decision 4: the client grades what we ship TODAY, tray included. A row
    an old run delivered but today's stage-2 drop (confidence below 0.99)
    removes must reach the bundle as a suggestion -- pos 0, suggested -- not
    vanish and not ship as a confirmed balloon. Its stale balloon is re-placed
    like today's pipeline places the tray (after the real balloons)."""
    low = Characteristic(pos=2, id="r2", raw_text="35 +0,2 -0,2", nominal="35",
                         kind="dimension", target_region=(300, 400, 380, 430),
                         balloon_xy=(1, 1), confidence=0.9)
    dump_path = _dump(tmp_path, sample_pdf, extra_rows=[low])
    entry = campaign_build.build_entry(dump_path, sample_pdf, tmp_path / "bundle",
                                       campaign="pilot-1", group="problem",
                                       run="r3-trainpredict", doc_hash="bb")
    rows = {r["id"]: r for r in json.loads((entry / "result.json").read_text())["rows"]}
    assert rows["r1"]["suggested"] is False and rows["r1"]["pos"] == 1
    assert rows["r2"]["suggested"] is True and rows["r2"]["pos"] == 0
    assert rows["r2"]["balloon_xy"] != [1, 1]


def test_page_png_is_rendered_at_the_dumps_exact_scale(tmp_path, sample_pdf):
    """A clamped render records a non-integer scale. Rounding it to an integer
    dpi would draw page.png at a slightly different scale than the boxes, so
    the page is rendered at scale * 72 exactly -- and the check is tight."""
    scale = 250.4 / 72.0
    dump_path = _dump(tmp_path, sample_pdf, scale=scale)
    entry = campaign_build.build_entry(dump_path, sample_pdf, tmp_path / "bundle",
                                       campaign="p", group="great", run="r",
                                       doc_hash="ee")
    import fitz
    with fitz.open(sample_pdf) as d:
        w_pt = d[0].rect.width
    with Image.open(entry / "page.png") as im:
        assert abs(im.width - w_pt * scale) <= 1


def test_a_render_at_another_scale_refuses_the_entry(tmp_path, sample_pdf, monkeypatch):
    """The review screen draws the rows' pixel boxes on page.png. If the page
    no longer renders at the dump's scale (the pixel budget clamped it), every
    box would sit in the wrong place -- the build must refuse rather than ship
    misaligned boxes, and must leave no half-written entry behind."""
    real = export_share.render_page

    def clamped(pdf_path, dpi, out_dir, **kw):
        return real(pdf_path, dpi=dpi / 2, out_dir=out_dir, **kw)
    monkeypatch.setattr(export_share, "render_page", clamped)
    out = tmp_path / "bundle"
    with pytest.raises(campaign_build.ScaleMismatch):
        campaign_build.build_entry(_dump(tmp_path, sample_pdf), sample_pdf, out,
                                   campaign="pilot-1", group="great",
                                   run="r5-control", doc_hash="cc")
    assert list(out.iterdir()) == []


def test_duplicate_or_empty_row_ids_refuse_the_entry(tmp_path, sample_pdf):
    """The journal addresses rows by id; two rows with one id (or none) would
    make every reviewer edit on them ambiguous."""
    twin = Characteristic(pos=2, id="r1", raw_text="35 +0,2 -0,2", nominal="35",
                          kind="dimension", target_region=(300, 400, 380, 430),
                          confidence=0.995)
    with pytest.raises(ValueError, match="row ids"):
        campaign_build.build_entry(_dump(tmp_path, sample_pdf, extra_rows=[twin]),
                                   sample_pdf, tmp_path / "bundle", campaign="p",
                                   group="great", run="r", doc_hash="dd")
    assert list((tmp_path / "bundle").iterdir()) == []


# --- main(): the operator's loop, on a fake root -----------------------------

def _root(tmp_path, sample_pdf, monkeypatch):
    monkeypatch.setenv("SINDRI_DOC_SALT", "testsalt")
    root = tmp_path / "root"
    run = root / "runs" / "r5-control"
    run.mkdir(parents=True)
    (root / "corpus" / "originals").mkdir(parents=True)
    for doc in ("BAD", "GOOD"):
        shutil.copyfile(sample_pdf, root / "corpus" / "originals" / f"{doc}{sample_pdf.suffix}")
    _dump(tmp_path, sample_pdf, doc_id="GOOD", run_dir=run)
    (run / f"BAD{export_share._PRED}").write_text("{not json", encoding="utf-8")
    anon = Anonymizer()
    hb, hg = anon("BAD"), anon("GOOD")
    man = tmp_path / "m.json"
    man.write_text(json.dumps({"runs": [{"run": "r5-control", "hashes": [hb, hg]}],
                               "roles": {hb: "problem", hg: "great"}}))
    return root, man, hb, hg


def test_a_failing_entry_is_skipped_by_type_and_the_rest_still_build(
        tmp_path, sample_pdf, monkeypatch, capsys):
    """An exception message from pydantic/fitz/the OS can carry client values
    or real ids, so only its TYPE is printed; one bad dump must not stop the
    campaign, and must leave no partial entry behind."""
    root, man, hb, hg = _root(tmp_path, sample_pdf, monkeypatch)
    out = tmp_path / "out"
    rc = campaign_build.main(["--root", str(root), "--manifest", str(man),
                              "--name", "pilot-1", "--out", str(out)])
    err = capsys.readouterr().err
    assert rc == 1
    assert f"SKIP {hb}: " in err and "not json" not in err and "BAD" not in err
    assert sorted(p.name for p in out.iterdir()) == [hg]
    assert (out / hg / "campaign.json").is_file()


def test_a_rerun_keeps_complete_entries(tmp_path, sample_pdf, monkeypatch, capsys):
    root, man, hb, hg = _root(tmp_path, sample_pdf, monkeypatch)
    out = tmp_path / "out"
    args = ["--root", str(root), "--manifest", str(man), "--name", "p", "--out", str(out)]
    campaign_build.main(args)
    before = (out / hg / "result.json").stat().st_mtime_ns
    capsys.readouterr()
    campaign_build.main(args)
    assert f"have {hg}" in capsys.readouterr().out
    assert (out / hg / "result.json").stat().st_mtime_ns == before


def test_an_incomplete_entry_is_named_not_overwritten(tmp_path, sample_pdf,
                                                      monkeypatch, capsys):
    """A folder without campaign.json (copied by hand, or from before entries
    were atomic) cannot be renamed over -- say so by hash and move on, rather
    than surface a bare OSError the operator cannot act on."""
    root, man, hb, hg = _root(tmp_path, sample_pdf, monkeypatch)
    out = tmp_path / "out"
    (out / hg).mkdir(parents=True)
    (out / hg / "page.png").write_bytes(b"")
    rc = campaign_build.main(["--root", str(root), "--manifest", str(man),
                              "--name", "p", "--out", str(out)])
    err = capsys.readouterr().err
    assert rc == 1
    assert f"SKIP {hg}: incomplete entry exists -- delete it" in err
    assert [p.name for p in (out / hg).iterdir()] == ["page.png"]


# --- the output folder guard (shared with export_share) ----------------------

def test_out_dir_inside_the_protected_root_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    for bad in (root, root / "x" / "y"):
        with pytest.raises(ValueError):
            export_share.check_out_dir(bad, root)
    export_share.check_out_dir(tmp_path / "elsewhere", root)


def test_out_dir_inside_a_git_tree_is_refused(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(ValueError):
        export_share.check_out_dir(tmp_path / "repo" / "share", tmp_path / "root")
    with pytest.raises(ValueError):
        export_share.check_out_dir(tmp_path / "repo", tmp_path / "root")


def test_both_tools_refuse_a_bad_out_dir_before_creating_it(tmp_path, sample_pdf,
                                                           monkeypatch):
    root, man, _, _ = _root(tmp_path, sample_pdf, monkeypatch)
    out = root / "share"
    assert campaign_build.main(["--root", str(root), "--manifest", str(man),
                                "--name", "p", "--out", str(out)]) == 2
    assert export_share.main(["--root", str(root), "--manifest", str(man),
                              "--out", str(out)]) == 2
    assert not out.exists()
