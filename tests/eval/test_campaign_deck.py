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
