"""C1 -> C2 end to end on one synthetic drawing: the operator's builder, the
loader, the worker, a review driven through the REAL ReviewStore and sealed
the way /seal seals it, the pull, the tar reader, the grader, the CLI and the
operator deck's example picker.

Every piece has its own tests, and C2's run on tars built by
tests/eval/campaign_fixtures.py -- which copy pull.py's layout by hand. This
one runs on records the real code wrote, so a fact renamed or dropped on one
side of a seam (a header field the loader no longer copies, an event stamp the
grader reads, a seal key) fails here rather than on the client's campaign,
after the client has done the work."""
import json

import fitz
import pytest

from app.batch import db
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.batch.worker import Worker
from app.campaign import load, pull
from app.eval import campaign_deck, runner
from app.eval.anon import Anonymizer
from app.eval.campaign_grade import DIGEST_KIND, REPORT_KIND, grade_campaign, px_to_mm
from app.eval.campaign_records import read_records
from app.eval.dump import save_dump
from app.eval.models import GoldCharacteristic, GoldDoc, PredictionDump, RunConfig
from app.models import Characteristic, ExtractionResult
from app.review.journal import net_events
from app.review.replay import replay
from app.review.store import ReviewStore

import campaign_build

SALT = "t"
DOC_ID = "DOC-1"
NOTE = "we never balloon chamfers"
SCAN_TEXT = "SCANTEXT-77"          # OCR text: client data, must never reach a digest
SCALE = 300 / 72.0
MOVE_PX = (60.0, 80.0)      # a 100 px bubble drag


def _row(i, box, raw):
    # Confidence above the stage-2 drop, a printed tolerance (no_tolerance
    # would flag it) and boxes far apart (tight_cluster, contained_duplicate):
    # the policy re-applied by campaign_build must leave all five delivered.
    return Characteristic(pos=i, id=f"r{i}", raw_text=raw, kind="dimension",
                          target_region=box, balloon_xy=(10, 10), confidence=0.995)


ROWS = [_row(1, (400, 400, 560, 470), "20 +0,1 -0,1"),
        _row(2, (400, 1200, 560, 1270), "35 +0,2 -0,2"),
        _row(3, (1200, 400, 1360, 470), "48 +0,1 -0,1"),
        _row(4, (1200, 1200, 1360, 1270), "12 +0,05 -0,05"),
        _row(5, (1800, 2400, 1960, 2470), "64 +0,3 -0,3")]


def _center_pt(box, rect):
    return ((box[0] + box[2]) / 2 / SCALE + rect[0],
            (box[1] + box[3]) / 2 / SCALE + rect[1])


def _review(store, sid):
    """What a reviewer's tab does: /open for a writer, journal events (stamped
    like journal.js: seq, t_wall, t_ms), then Finish -> /seal with the rows
    the tab holds -- the replayed state, posted as Characteristic -- and the
    ids it holds as reviewed."""
    def scan(row_id, text):
        return {"type": "scan_read", "row_id": row_id, "box": [2400, 600, 2500, 660],
                "text": text, "conf": 0.9, "ms": 300, "empty": False}

    def add_manual(row_id, nominal, x):
        return {"type": "add_row", "row": Characteristic(
            pos=9, id=row_id, nominal=nominal, char_type="Distance", source="manual",
            kind="dimension", target_region=(x, 600, x + 100, 660),
            balloon_xy=(x, 560)).model_dump(mode="json")}

    o = store.open(sid)
    prop = {r["id"]: r for r in o["proposal"]}
    bx, by = prop["r3"]["balloon_xy"]
    b4 = prop["r4"]["target_region"]
    ops = [
        {"type": "edit_cell", "id": "r1", "field": "nominal",
         "old": prop["r1"]["nominal"], "new": "21", "reason": "misread_digit"},
        {"type": "delete_row", "id": "r2", "reason": "not_a_characteristic"},
        {"type": "move_row", "id": "r3", "xy": [bx + MOVE_PX[0], by + MOVE_PX[1]]},
        {"type": "move_target", "id": "r4", "box": [b4[0] + 2, b4[1], b4[2] + 2, b4[3]]},
        {"type": "add_row", "row": Characteristic(
            pos=6, id="m1", nominal="7", char_type="Distance", source="manual",
            kind="dimension", target_region=(2400, 600, 2500, 660),
            balloon_xy=(2450, 560)).model_dump(mode="json")},
        {"type": "set_note", "text": NOTE},
        {"type": "accept", "ids": ["r3"]},
        # Scan-rectangle attempts (review UX plan): kept untouched, kept then
        # edited, one whose add is undone below (its scan_read must survive
        # compaction), and a failed request. Then a reviewer flag on r5.
        scan("m2", "9"), add_manual("m2", "9", 3000),
        scan("m3", SCAN_TEXT), add_manual("m3", "5", 3200),
        {"type": "edit_cell", "id": "m3", "field": "nominal", "old": "5", "new": "6",
         "reason": "misread_digit"},
        scan("m4", "7"), add_manual("m4", "7", 3400),
        {"type": "retract", "target": 14},      # the seq of m4's add_row
        {"type": "scan_read", "row_id": None, "box": [5, 5, 50, 40], "text": "",
         "conf": None, "ms": 900, "empty": True, "failed": True},
        {"type": "flag", "ids": ["r5"]},
    ]
    events = [{"seq": i, "t_wall": 1_700_000_000_000 + 1000 * i, "t_ms": 1000 * i, **e}
              for i, e in enumerate(ops, 1)]
    assert store.append(sid, o["writer"], events) == len(events)
    state = replay(o["proposal"], net_events(events))
    rows = [Characteristic.model_validate(
        {k: v for k, v in r.items() if k in Characteristic.model_fields}).model_dump(mode="json")
        for r in state.values()]
    reviewed = [i for i, r in state.items() if r["reviewed"]]
    out = store.seal(sid, o["writer"], len(events), rows, reviewed)
    assert out["replay_ok"] is True
    return out["revision"], len(events), prop


def _no_leak(text):
    for token in ('"upper_tol"', '"lower_tol"', '"characteristics"', '"position_pt"'):
        assert token not in text
    assert f"{DOC_ID}.pdf" not in text and NOTE not in text and SCAN_TEXT not in text


@pytest.fixture
def chain(tmp_path, sample_pdf, monkeypatch):
    monkeypatch.setenv("SINDRI_DOC_SALT", SALT)
    h = Anonymizer(salt=SALT)(DOC_ID)
    with fitz.open(sample_pdf) as pdf:
        r = pdf[0].rect
        rect = (r.x0, r.y0, r.x1, r.y1)
    dump_path = save_dump(
        PredictionDump(doc_id=DOC_ID, config=RunConfig(), scale=SCALE, page_rect=rect,
                       result=ExtractionResult(characteristics=ROWS)),
        tmp_path / "runs" / "r5-control")
    bundle = tmp_path / "bundle"
    campaign_build.build_entry(dump_path, sample_pdf, bundle, campaign="pilot-1",
                               group="great", run="r5-control", doc_hash=h)

    data = DataDir(tmp_path / "data")
    conn = db.connect(data.db_path)
    db.migrate(conn)
    repo, store = Repo(conn), ReviewStore(tmp_path / "reviews")

    def no_gpu(*a):
        raise AssertionError("a campaign drawing must never be extracted")
    worker = Worker(repo, data, store, no_gpu, lambda pdf: {"h": 1})
    tar = tmp_path / "prot" / "pilot-1.tar"
    tar.parent.mkdir()
    try:
        assert load.load_bundle(bundle, data, repo)["loaded"] == 1
        assert worker.run_once() is not None
        [doc] = repo.list_documents()
        rev, final_seq, prop = _review(store, doc["id"])
        repo.mark_finished(doc["id"], rev, final_seq)
        assert pull.pull_campaign("pilot-1", repo, store, data, tar) == 1
    finally:
        worker.stop()
        conn.close()

    # Gold of the SAME page, as the --originals ingest would write it: r1's
    # balloon carries the value the client corrected to, r5's the proposed
    # one; r2-r4 and the manual add are not ballooned.
    def gold_row(n, rid, **kw):
        p = prop[rid]
        return GoldCharacteristic(
            balloon=n, position_pt=_center_pt(p["target_region"], rect),
            **{f: p[f] for f in ("char_type", "nominal", "upper_tol", "lower_tol")}, **kw)
    gold = GoldDoc(doc_id=DOC_ID, pdf="x", excel="y", page_rect=rect,
                   characteristics=[gold_row(1, "r1"), gold_row(2, "r5")])
    gold.characteristics[0].nominal = "21"
    return {"tmp": tmp_path, "tar": tar, "hash": h, "gold": gold, "prop": prop,
            "final_seq": final_seq}


def _manifest(h):
    return {"runs": [{"run": "r5-control", "hashes": [h]}], "roles": {h: "great"},
            "pool": {"great": 3, "problem": 2}}


def test_real_records_grade_as_the_journal_implies(chain):
    h, prop = chain["hash"], chain["prop"]
    assert sorted(prop) == ["r1", "r2", "r3", "r4", "r5"]   # the policy kept all five
    records = read_records(chain["tar"])
    [d] = records.drawings.values()
    # The facts grading needs must survive load -> worker header -> pull.
    facts = d.header["campaign"]
    assert facts["render_scale"] == SCALE and facts["doc_hash"] == h
    assert len(facts["page_rect"]) == 4
    assert all(isinstance(e.get("t_ms"), int) for e in d.events)

    report, digest = grade_campaign(records, {h: chain["gold"]}, _manifest(h))
    assert digest["drawings"]["approved"] == 1 and digest["reconciles"] is True
    [doc] = report["drawings"]
    rows = {r["id"]: r for r in doc["rows"]}
    assert rows["r1"]["grade"] == "edited" and rows["r1"]["fields"] == ["nominal"]
    assert rows["r1"]["reason"] == "misread_digit"
    assert rows["r2"]["grade"] == "phantom" and rows["r2"]["reason"] == "not_a_characteristic"
    assert rows["r3"]["grade"] == "confirmed" and rows["r3"]["reviewed"] is True
    assert rows["r5"]["grade"] == "confirmed" and rows["r5"]["reviewed"] is False
    assert [m["id"] for m in doc["missed"]] == ["m1", "m2", "m3"]
    assert [m["via"] for m in doc["missed"]] == ["typed", "scan", "scan"]
    # Placement: px in the proposal's page image, mm through render_scale.
    assert rows["r3"]["bubble_px"] == pytest.approx(100.0)
    mm = px_to_mm(100.0, SCALE)
    assert mm == pytest.approx(100 / SCALE * 25.4 / 72)
    assert digest["groups"]["great"]["placement"]["moved_bubble_mm_median"] == pytest.approx(mm)
    assert rows["r4"]["anchor_moved"] is True and rows["r4"]["anchor_iou"] > 0.9
    assert rows["r5"]["bubble_px"] == 0 and rows["r5"]["anchor_moved"] is False
    c = digest["groups"]["great"]["counts"]
    assert (c["confirmed"], c["edited"], c["phantom"], c["missed"]) == (3, 1, 1, 3)
    assert (c["missed_scan"], c["missed_typed"], c["missed_via_unknown"]) == (2, 1, 0)
    # The reviewer's own flag on r5 (confirmed unchanged, never accepted).
    assert (c["manual_flagged"], c["manual_flagged_confirmed"]) == (1, 1)
    assert rows["r5"]["manual_flag"] is True and rows["r3"]["manual_flag"] is False
    # Four scan attempts: one untouched, one edited, one undone (its add is
    # gone from the net journal, the attempt is not), one failed request.
    s = digest["groups"]["great"]["scans"]
    assert (s["attempts"], s["untouched"], s["edited"], s["undone"], s["failed"],
            s["deleted"]) == (4, 1, 1, 1, 1, 0)
    assert s["edits"] == {"fields:nominal": 1} and s["empty"] == 1
    assert s["ms_median"] == 300 and s["ms_max"] == 900
    assert [x["outcome"] for x in doc["scans"]] == ["untouched", "edited", "undone", "failed"]
    assert doc["scans"][1]["reason"] == "misread_digit"
    assert (c["confirmed_explicit"], c["confirmed_silent"]) == (1, 2)
    assert doc["time_ms"] == 1000 * (chain["final_seq"] - 1)
    assert doc["note"] == NOTE and digest["notes_written"] == 1

    cross = doc["cross"]
    assert cross["computed"] is True
    assert cross["verdicts"]["r1"] == "gold_wrong" and cross["verdicts"]["r5"] == "gold_correct"
    assert cross["verdicts"]["r2"] == "gold_phantom"
    assert cross["neither_found"] == 0 and cross["matched_final"] == 2

    # Only a great drawing was graded, so the pool's problem share is unmeasured
    # and says so rather than pooling one group as if it were the mix.
    assert digest["pooled"]["precision"] is None
    assert digest["pooled_unmeasured"]["pool_groups_missing"] == ["problem"]

    ex = campaign_deck.examples(report)
    counts = {r["bucket"]: r["count"]
              for kind in ("value", "placement") for r in digest["findings"][kind]}
    assert {b: v["total"] for b, v in ex["buckets"].items()} == counts
    assert counts == {"fields:nominal": 1, "phantom:not_a_characteristic": 1,
                      "missed": 3, "bubble_moved": 1, "anchor_nudged": 1}


def test_the_cli_writes_the_same_grade_inside_and_a_safe_digest_outside(
        chain, monkeypatch, capsys):
    tmp, h = chain["tmp"], chain["hash"]
    prot = chain["tar"].parent
    roots = tmp / "protected-paths"
    roots.write_text(f"{prot}\n")
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(roots))
    gold_dir = tmp / "gold"
    gold_dir.mkdir()
    (gold_dir / f"{DOC_ID}.gold.json").write_text(chain["gold"].model_dump_json())
    man = tmp / "manifest.json"
    man.write_text(json.dumps(_manifest(h)))
    # Since review round 2 the report is a direct child of <root>/reports/.
    out = prot / "reports" / "pilot-1.grade.json"
    gout = tmp / "eval" / "pilot-1-grade.json"
    rc = runner.main(["score", "--reviewer-grade", str(chain["tar"]), "--gold", str(gold_dir),
                      "--manifest", str(man), "--name", "pilot-1", "--out", str(out),
                      "--grade-out", str(gout)])
    assert rc == 0
    cap = capsys.readouterr()
    assert cap.out.strip() == ("reviewer-grade: approved=1 pending=0 ungradable=0 "
                               "skipped=0 not_in_records=0 gold=1/1 reconciles=True")
    _no_leak(cap.out + cap.err)

    text = gout.read_text()
    _no_leak(text)
    digest = json.loads(text)
    report = json.loads(out.read_text())
    assert digest["kind"] == DIGEST_KIND and report["kind"] == REPORT_KIND
    assert report["digest"] == digest
    assert digest["gold_cross_check"]["n_drawings_computed"] == 1
    assert digest["by_source_run"] == {"r5-control": 1}
    ex = campaign_deck.examples(report)
    counts = {r["bucket"]: r["count"]
              for kind in ("value", "placement") for r in digest["findings"][kind]}
    assert {b: v["total"] for b, v in ex["buckets"].items()} == counts
    # 3 confirmed of 5 delivered: the silent confirmation of r5 counts too.
    assert digest["groups"]["great"]["precision"] == pytest.approx(3 / 5)
