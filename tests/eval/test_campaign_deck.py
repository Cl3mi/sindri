"""Operator drill-down: which example rows each correction bucket shows.

The examples must come from the SAME bucket definitions as the digest's ranked
findings, or a bucket's page could list rows its own count never included."""
from app.eval.campaign_deck import examples
from app.eval.campaign_grade import ranked_findings

S = 300 / 72.0


def g(id, grade, *, fields=(), reason=None, bubble_px=0.0, iou=1.0, moved=False):
    return {"id": id, "grade": grade, "fields": list(fields), "reason": reason,
            "flagged": False, "suggested": grade.startswith("suggestion"),
            "reviewed": True, "kind": "dimension", "bubble_px": bubble_px,
            "anchor_iou": iou, "anchor_moved": moved}


def row(id, **kw):
    return {"id": id, "nominal": "10", "target_region": [10, 10, 50, 30],
            "balloon_xy": [100, 100], **kw}


def doc(h, rows, missed=(), *, cross=None, note="", group="great", status="approved"):
    return {"doc_hash": h, "group": group, "status": status, "scale": S,
            "rows": rows, "missed": list(missed), "cross": cross, "note": note,
            "proposal": [row(r["id"]) for r in rows],
            "final": [row(r["id"], nominal="12") for r in rows
                      if r["grade"] not in ("phantom", "suggestion_deleted")]
                     + list(missed)}


def test_examples_pick_the_top_rows_per_bucket_with_their_boxes():
    prop = {"id": "a", "nominal": "10", "target_region": [10, 10, 50, 30],
            "balloon_xy": [100, 100]}
    fin = {**prop, "nominal": "12", "balloon_xy": [400, 100]}
    report = {"drawings": [{
        "doc_hash": "aaaaaaaa", "group": "great", "scale": S, "status": "approved",
        "rows": [g("a", "edited", fields=["nominal"], reason="misread_digit",
                   bubble_px=300.0)],
        "missed": [], "proposal": [prop], "final": [fin], "cross": None}]}
    ex = examples(report, per_bucket=5)["buckets"]
    assert [e["row_id"] for e in ex["fields:nominal"]["examples"]] == ["a"]
    e = ex["fields:nominal"]["examples"][0]
    assert e["proposed"]["nominal"] == "10" and e["final"]["nominal"] == "12"
    assert e["reason"] == "misread_digit" and e["doc_hash"] == "aaaaaaaa"
    assert e["verdict"] is None and e["note"] == "" and e["scale"] == S
    assert ex["bubble_moved"]["examples"][0]["mm"] > 20


def test_total_counts_every_row_while_examples_are_capped():
    rows = [g(f"r{i}", "edited", fields=["nominal"]) for i in range(7)]
    out = examples({"drawings": [doc("aaaaaaaa", rows)]}, per_bucket=3)
    b = out["buckets"]["fields:nominal"]
    assert b["total"] == 7 and len(b["examples"]) == 3
    # report order, then row id: a stable, testable pick
    assert [e["row_id"] for e in b["examples"]] == ["r0", "r1", "r2"]


def test_placement_orders_by_displacement_and_anchors_by_iou():
    rows = [g("a", "confirmed", bubble_px=100.0), g("b", "confirmed", bubble_px=900.0),
            g("c", "confirmed", bubble_px=500.0),
            g("d", "confirmed", iou=0.2, moved=True), g("e", "confirmed", iou=0.05, moved=True),
            g("f", "confirmed", iou=0.6, moved=True)]
    out = examples({"drawings": [doc("aaaaaaaa", rows)]})["buckets"]
    assert [e["row_id"] for e in out["bubble_moved"]["examples"]] == ["b", "c", "a"]
    assert [e["row_id"] for e in out["anchor_repointed"]["examples"]] == ["e", "d"]
    assert [e["row_id"] for e in out["anchor_nudged"]["examples"]] == ["f"]


def test_a_missed_row_has_no_proposal_and_carries_the_clients_row():
    missed = [row("m1", nominal="7")]
    out = examples({"drawings": [doc("aaaaaaaa", [g("a", "confirmed")], missed)]})["buckets"]
    e = out["missed"]["examples"][0]
    assert e["proposed"] is None and e["final"]["nominal"] == "7"
    assert e["row_id"] == "m1" and e["grade"] == "missed" and e["reason"] == "none"


def test_a_phantom_has_no_final_and_an_unknown_reason_reads_as_other():
    rows = [g("p", "phantom", reason="a client typed this"),
            g("q", "phantom", reason="duplicate"), g("r", "phantom")]
    out = examples({"drawings": [doc("aaaaaaaa", rows)]})["buckets"]
    p = out["phantom:other"]["examples"][0]
    assert p["final"] is None and p["proposed"]["nominal"] == "10"
    assert p["reason"] == "other"
    assert "phantom:duplicate" in out and "phantom:none" in out


def test_an_unknown_edit_reason_reads_as_other_too():
    rows = [g("a", "edited", fields=["nominal"], reason="free text with a value 12.5")]
    e = examples({"drawings": [doc("aaaaaaaa", rows)]})["buckets"]["fields:nominal"]["examples"][0]
    assert e["reason"] == "other"


def test_ungradable_and_pending_drawings_are_skipped():
    bad = {"doc_hash": "bbbbbbbb", "group": "great", "status": "ungradable", "rows": None,
           "missed": None, "scale": None, "proposal": None, "final": None, "cross": None,
           "note": None}
    pend = {**bad, "doc_hash": "cccccccc", "status": "pending"}
    out = examples({"drawings": [bad, pend, doc("aaaaaaaa", [g("a", "phantom")])]})
    assert list(out["buckets"]) == ["phantom:none"]
    assert out["buckets"]["phantom:none"]["examples"][0]["doc_hash"] == "aaaaaaaa"


def test_verdict_and_note_come_from_the_drawing():
    cross = {"computed": True, "verdicts": {"a": "gold_wrong"}}
    d = doc("aaaaaaaa", [g("a", "edited", fields=["nominal"]),
                         g("b", "edited", fields=["nominal"])], cross=cross, note="hello")
    ex = examples({"drawings": [d]})["buckets"]["fields:nominal"]["examples"]
    assert [(e["row_id"], e["verdict"], e["note"]) for e in ex] == [
        ("a", "gold_wrong", "hello"), ("b", None, "hello")]
    d["cross"] = {"computed": False, "reason": "no_gold"}
    ex = examples({"drawings": [d]})["buckets"]["fields:nominal"]["examples"]
    assert all(e["verdict"] is None for e in ex)


def test_totals_and_order_equal_the_digest_counts_for_every_bucket():
    """The single-source-of-truth guarantee: row_buckets feeds both."""
    d1 = doc("aaaaaaaa", [
        g("a", "edited", fields=["nominal"], bubble_px=300.0),
        g("b", "edited", fields=["upper_tol", "lower_tol"], iou=0.1, moved=True),
        g("c", "phantom", reason="duplicate"), g("d", "phantom", reason="zzz"),
        g("e", "suggestion_confirmed"), g("f", "suggestion_deleted"),
        g("g", "suggestion_edited", fields=["char_type"]),
        g("h", "confirmed", iou=0.9, moved=True, bubble_px=0.2)],
        missed=[row("m1"), row("m2")])
    d2 = doc("dddddddd", [g("a", "edited", fields=["nominal"], bubble_px=80.0),
                          g("x", "confirmed", iou=None, moved=True)], missed=[row("m3")])
    drawings = [d1, d2]
    digest = ranked_findings(drawings)
    out = examples({"drawings": drawings}, per_bucket=2)
    want = {r["bucket"]: r["count"] for k in ("value", "placement") for r in digest[k]}
    assert {b: v["total"] for b, v in out["buckets"].items()} == want
    assert out["order"] == [r["bucket"] for k in ("value", "placement") for r in digest[k]]
    assert all(len(v["examples"]) <= 2 for v in out["buckets"].values())


# --- the deck server (Task 11) ------------------------------------------------
# It serves client values to the OPERATOR's browser, so the refusals matter
# most: no token, a foreign Host, a free-form id. The fixture drawing is the
# repo's synthetic sample, written under a stem whose salted hash is the
# report's doc_hash -- exactly how the operator's originals dir resolves.
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app.eval import review_crops
from app.eval.anon import Anonymizer
from app.eval.campaign_deck import (MSG_FRAME, MSG_NO_DRAWING, CampaignDeckApp,
                                    make_server)
from app.eval.campaign_grade import REPORT_KIND
from app.eval.review import ReviewRefused
from tests.eval.test_review import _roots

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / ("sample" + ".pdf")
STEM = "PART-4711"
HASH = Anonymizer(salt="t")(STEM)
NOFILE = Anonymizer(salt="t")("NO-SUCH-PART")
XSS = '<img src=x onerror="alert(1)">'
PAGE = [0.0, 0.0, 842.0, 595.0]


def _report(page_rect=PAGE, note=XSS):
    d1 = doc(HASH, [g("a", "edited", fields=["nominal"], bubble_px=300.0)], note=note)
    d1["final"][0]["nominal"] = XSS
    d1["final"][0]["balloon_xy"] = [400, 100]
    d1["page_rect"] = page_rect
    d2 = doc(NOFILE, [g("a", "phantom", reason="duplicate")])
    d2["page_rect"] = PAGE
    drawings = [d1, d2]
    return {"kind": REPORT_KIND, "campaign": "pilot-1", "drawings": drawings,
            "digest": {"findings": ranked_findings(drawings)}}


def _write(root, report, name="c.grade.json"):
    path = root / name
    path.write_text(json.dumps(report, ensure_ascii=False))
    return path


@pytest.fixture
def deck_env(tmp_path, monkeypatch):
    prot = tmp_path / "prot"
    pdfs = prot / "originals"
    pdfs.mkdir(parents=True)
    (pdfs / (STEM + SAMPLE.suffix)).write_bytes(SAMPLE.read_bytes())
    _roots(tmp_path, monkeypatch, prot)
    monkeypatch.setenv("SINDRI_DOC_SALT", "t")
    return prot, pdfs


@pytest.fixture
def deck(deck_env):
    prot, pdfs = deck_env
    app = CampaignDeckApp(_write(prot, _report()), pdfs)
    srv = make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(url, **headers):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                  timeout=5)


def _status(url, **headers):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(url, **headers)
    return e.value.code, json.loads(e.value.read())


def test_every_route_refuses_a_missing_or_wrong_token(deck):
    """Another local process or a page in the same browser cannot read
    client values without the token printed on the operator's terminal."""
    app, base = deck
    for path in ("/", "/api/examples", f"/crop?doc={HASH}&row=a",
                 "/api/examples?t=wrong", f"/crop?doc={HASH}&row=a&t=x"):
        assert _status(base + path)[0] == 403


def test_a_foreign_host_header_is_refused(deck):
    """DNS rebinding: a page on another name resolved to 127.0.0.1."""
    app, base = deck
    code, _ = _status(f"{base}/api/examples?t={app.token}", Host="evil.example:80")
    assert code == 403


def test_examples_are_json_with_nosniff_and_values_stay_data(deck):
    app, base = deck
    r = _get(f"{base}/api/examples?t={app.token}")
    assert r.headers["Content-Type"].startswith("application/json")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    body = json.load(r)
    ex = body["buckets"]["fields:nominal"]["examples"][0]
    assert ex["doc_hash"] == HASH and ex["final"]["nominal"] == XSS and ex["note"] == XSS
    assert body["order"][0] in body["buckets"]
    assert body["levers"]["fields:nominal"]["lever"]


def test_the_page_is_self_contained_and_renders_values_only_as_text(deck):
    """A note or value holding markup must stay inert: the page may set client
    text only through textContent/setAttribute, never innerHTML/outerHTML/
    insertAdjacentHTML/document.write, and must make no external request."""
    app, base = deck
    r = _get(f"{base}/?t={app.token}")
    assert r.headers["Content-Type"].startswith("text/html")
    html = r.read().decode()
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                   "http://", "https://", "//cdn", "<link", "@import", "eval("):
        assert banned not in html, banned
    for route in ("/api/examples", "/crop"):
        assert route in html
    assert XSS not in html and "textContent" in html


def test_a_crop_is_cut_from_the_original_drawing(deck):
    app, base = deck
    r = _get(f"{base}/crop?doc={HASH}&row=a&t={app.token}")
    assert r.headers["Content-Type"] == "image/png"
    png = r.read()
    assert png.startswith(b"\x89PNG")
    assert png != review_crops.placeholder_png(MSG_NO_DRAWING)
    assert png != review_crops.placeholder_png(MSG_FRAME)


def test_a_drawing_with_no_pdf_gets_the_placeholder(deck):
    app, base = deck
    png = _get(f"{base}/crop?doc={NOFILE}&row=a&t={app.token}").read()
    assert png == review_crops.placeholder_png(MSG_NO_DRAWING)


def test_a_page_frame_mismatch_gets_the_placeholder_not_a_wrong_crop(deck_env):
    """The report's px are in the CAMPAIGN's page frame; on a different sheet
    every box lands somewhere unrelated, so no crop is cut at all."""
    prot, pdfs = deck_env
    app = CampaignDeckApp(_write(prot, _report(page_rect=[0, 0, 1191, 842])), pdfs)
    assert app.crop(HASH, "a") == review_crops.placeholder_png(MSG_FRAME)
    # Within the 1 pt rounding tolerance it is the same page.
    app = CampaignDeckApp(_write(prot, _report(page_rect=[0, 0, 842.5, 595.5])), pdfs)
    assert app.crop(HASH, "a") != review_crops.placeholder_png(MSG_FRAME)


def test_a_report_without_a_page_frame_gets_the_placeholder(deck_env):
    prot, pdfs = deck_env
    app = CampaignDeckApp(_write(prot, _report(page_rect=None)), pdfs)
    assert app.crop(HASH, "a") == review_crops.placeholder_png(MSG_FRAME)


@pytest.mark.parametrize("query", [
    "doc=ffffffff&row=a", f"doc={HASH}&row=zz", "doc=../../etc&row=a",
    f"doc={HASH}", "row=a", f"doc={HASH.upper()}&row=a"])
def test_unknown_or_free_form_ids_are_a_fixed_404(deck, query):
    """Only (doc, row) pairs the examples actually show can be cropped: no
    path, no arbitrary lookup, and the answer never echoes the request."""
    app, base = deck
    code, body = _status(f"{base}/crop?{query}&t={app.token}")
    assert code == 404 and body == {"error": "not found"}


def test_an_unknown_route_is_a_fixed_404(deck):
    app, base = deck
    assert _status(f"{base}/x?t={app.token}") == (404, {"error": "not found"})


def test_the_crop_spans_both_boxes_and_both_bubbles(deck_env, monkeypatch):
    """Union of the proposed and final anchors and bubbles, in POINTS via the
    drawing's scale and page origin; red proposed box, blue final dot."""
    prot, pdfs = deck_env
    seen = {}

    def fake(pdf, rect, pred_box_pt=None, gold_pt=None, final_box_pt=None):
        seen.update(rect=rect, pred=pred_box_pt, gold=gold_pt, fbox=final_box_pt)
        return b"\x89PNG"
    monkeypatch.setattr(review_crops, "render_crop", fake)
    app = CampaignDeckApp(_write(prot, _report()), pdfs)
    assert app.crop(HASH, "a") == b"\x89PNG"
    k = 72 / 300.0
    assert seen["pred"] == pytest.approx((10 * k, 10 * k, 50 * k, 30 * k))
    assert seen["fbox"] == pytest.approx((10 * k, 10 * k, 50 * k, 30 * k))
    assert seen["gold"] == pytest.approx((400 * k, 100 * k))
    x0, y0, x1, y1 = seen["rect"]
    # Padded around the union: the far final bubble (96 pt) and the box at 2.4 pt.
    assert x0 <= 10 * k and x1 >= 400 * k + review_crops.PAD_PT - 1e-6
    assert y0 <= 10 * k and y1 >= 100 * k + review_crops.PAD_PT - 1e-6


def test_the_app_never_writes_a_file(deck_env):
    """No crop cache, no copy of the report: the deck holds no files."""
    prot, pdfs = deck_env
    report = _write(prot, _report())
    before = sorted(prot.parent.rglob("*"))
    app = CampaignDeckApp(report, pdfs)
    app.crop(HASH, "a")
    app.crop(NOFILE, "a")
    app.payload()
    assert sorted(prot.parent.rglob("*")) == before


def test_the_app_refuses_a_report_outside_a_protected_root(deck_env, tmp_path):
    prot, pdfs = deck_env
    with pytest.raises(ReviewRefused):
        CampaignDeckApp(_write(tmp_path, _report()), pdfs)
