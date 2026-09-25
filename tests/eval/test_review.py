"""The operator's gdt review: a deck only a human opens, a tally an agent may read.

`gdt` has 8 rows wrong in char_type ONLY -- the one bucket where a single-field
fix passes the last-fault test. The GPU-free diagnostics settle half of why:
all 8 were predicted Flatness and all 8 are `parser._gdt_type`'s DEFAULT, so
the prediction is a guess on every row. What they cannot settle is what the
drawing shows, whether the symbol is in the transcription in some form the
parser does not map, and whether gold's label names that characteristic. Those
need the drawing and gold's label -- client content an agent must never see.

So the work is split along the data boundary:

  * the DECK carries client text (label, transcription, part number) and may
    only be written inside a protected root, which the agent's guard refuses
    to read. `score` writes it -- already sanctioned, so nothing widens the
    guard. The operator opens it with `review-serve` (docs/plans/
    2026-09-25-gdt-review-ui-design.md).
  * the ANSWERS live beside the deck; the TALLY reads only their closed-
    vocabulary choices and emits counts. Notes are never read.

The row set must be exactly the digest's (`report.is_char_type_only`), or the
operator reviews a different set from the one the counts describe.
"""
import json

import pytest

from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.review import (KIND, ReviewRefused, build_deck, collect_gdt_rows,
                             deck_sha, load_deck, write_deck)
from app.eval.score import score_doc
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)
_GDT = dict(kind="gdt", char_type="Flatness", nominal="0", upper_tol="0,05",
            lower_tol="0")
GOLD_POSITION = dict(char_type="Position", nominal="0", upper_tol="0,05",
                     lower_tol="0")


def _box(x, y):
    return (SCALE * (x - 15), SCALE * (y - 5), SCALE * (x + 15), SCALE * (y + 5))


def _doc(doc_id, rows):
    """rows: (x, y, pred_kwargs, gold_kwargs), one matched pair each."""
    gold, preds = [], []
    for i, (x, y, pk, gk) in enumerate(rows):
        gold.append(GoldCharacteristic(balloon=i + 1, position_pt=(x, y), **gk))
        preds.append(Characteristic(pos=i + 1, target_region=_box(x, y), **pk))
    g = GoldDoc(doc_id=doc_id, pdf=f"{doc_id}.pdf", excel=f"{doc_id}.xlsx",
                page_rect=RECT, characteristics=gold)
    d = PredictionDump(doc_id=doc_id, config=RunConfig(model_id="stub", dpi=300),
                       scale=SCALE, page_rect=RECT,
                       result=ExtractionResult(characteristics=preds))
    return d, g


def _collect(*docs):
    dumps, golds, scores = {}, {}, []
    for d, g in docs:
        dumps[d.doc_id], golds[g.doc_id] = d, g
        scores.append(score_doc(d, g, ReviewCostWeights(), MatchParams()))
    return collect_gdt_rows(dumps, golds, scores)


def _rows3():
    return _collect(
        _doc("P1", [(100, 100, dict(_GDT, raw_text="0,05 A"), GOLD_POSITION),
                    (900, 100, dict(_GDT, raw_text="0,1 B"), GOLD_POSITION)]),
        _doc("P2", [(100, 700, dict(_GDT, raw_text="0,02"), GOLD_POSITION)]))


def _roots(tmp_path, monkeypatch, *roots):
    f = tmp_path / "protected-paths"
    f.write_text("".join(f"{r}\n" for r in roots))
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(f))


# --- which rows, and what each carries ----------------------------------------

def test_it_selects_exactly_the_gdt_rows_wrong_in_char_type_only():
    """Same predicate as the digest's `wrong_fields` -- not the correct row,
    not the row with a second fault a char_type fix cannot free, and not a
    dimension row."""
    rows = _collect(_doc("P1", [
        (100, 100, dict(_GDT, raw_text="0,05 A"), GOLD_POSITION),
        (300, 100, dict(_GDT, raw_text="0,05 A", upper_tol="9"), GOLD_POSITION),
        (500, 100, dict(_GDT, raw_text="⏥ 0,05"),
         dict(GOLD_POSITION, char_type="Flatness")),
        (700, 100, dict(char_type="Distance", nominal="20", raw_text="20"),
         dict(char_type="Diameter", nominal="20")),
    ]))
    assert [(r.id, r.doc_id, r.balloon) for r in rows] == [("g1", "P1", 1)]


def test_each_row_carries_geometry_in_page_points():
    """The crop is cut from the PDF, so the pipeline's box must be in PDF
    points: target_region is in render pixels and is divided by the scale."""
    r = _collect(_doc("P1", [
        (100, 100, dict(_GDT, raw_text="0,05 A"), GOLD_POSITION)]))[0]
    assert r.gold_pt == (100, 100)
    assert r.pred_box_pt == pytest.approx((85, 95, 115, 105))
    assert r.parser_defaulted is True and r.scorer_reads_gold_as == "Position"
    assert r.where == "upper left" and r.gold_label == "Position"
    assert r.transcription == "0,05 A" and r.predicted == "Flatness"


def test_ids_follow_document_then_balloon_order():
    """Grouped by drawing so the operator's eye stays on one sheet at a time."""
    rows = _collect(
        _doc("P2", [(100, 700, dict(_GDT, raw_text="0,05 A"), GOLD_POSITION)]),
        _doc("P1", [(900, 100, dict(_GDT, raw_text="0,05 A"), GOLD_POSITION),
                    (100, 100, dict(_GDT, raw_text="0,05 B"), GOLD_POSITION)]))
    assert [(r.id, r.doc_id, r.balloon) for r in rows] == [
        ("g1", "P1", 1), ("g2", "P1", 2), ("g3", "P2", 1)]
    assert [r.where for r in rows] == ["upper right", "upper left", "lower left"]


# --- the deck -----------------------------------------------------------------

def test_the_deck_carries_its_questions_so_the_page_needs_no_code_per_review():
    deck = build_deck(_rows3(), run="r", originals_dir="/o", stamped_dir="/s")
    assert deck["kind"] == KIND and deck["version"] == 1
    assert [q["key"] for q in deck["questions"]] == [
        "drawing_shows", "symbol_in_transcription", "gold_label_means_it"]
    assert [r["id"] for r in deck["rows"]] == ["g1", "g2", "g3"]
    assert deck["originals_dir"] == "/o" and deck["stamped_dir"] == "/s"


def test_a_deck_round_trips_and_its_sha_is_stable(tmp_path, monkeypatch):
    root = tmp_path / "client"
    root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    deck = build_deck(_rows3(), run="r", originals_dir="/o", stamped_dir="/s")
    write_deck(root / "d.json", deck)
    back = load_deck(root / "d.json")
    assert deck_sha(back) == deck_sha(json.loads(json.dumps(deck)))


def test_the_deck_is_written_only_inside_a_protected_root(tmp_path, monkeypatch):
    """It holds client text. Inside a protected root the agent's guard refuses
    to read it; anywhere else -- docs/, the scratchpad -- an agent could. A
    path that only LOOKS inside (`..`, a `-copy` sibling) is outside."""
    root = tmp_path / "client"
    root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    deck = build_deck(_rows3(), run="r", originals_dir="/o", stamped_dir="/s")
    with pytest.raises(ReviewRefused, match="protected root"):
        write_deck(tmp_path / "outside.json", deck)
    for bad in (root / ".." / "elsewhere.json",
                tmp_path / "client-copy" / "x.json"):
        with pytest.raises(ReviewRefused):
            write_deck(bad, deck)


def test_no_protected_roots_configured_means_refuse(tmp_path, monkeypatch):
    """No roots file means nowhere is known to be safe -- refuse, never guess."""
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(tmp_path / "missing"))
    with pytest.raises(ReviewRefused):
        write_deck(tmp_path / "x.json", build_deck([], "r", "/o", "/s"))


def test_a_deck_is_never_overwritten(tmp_path, monkeypatch):
    """Answers are keyed to the deck; replacing it would orphan them."""
    root = tmp_path / "client"
    root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    deck = build_deck(_rows3(), run="r", originals_dir="/o", stamped_dir="/s")
    write_deck(root / "d.json", deck)
    with pytest.raises(ReviewRefused, match="exists"):
        write_deck(root / "d.json", deck)


def test_a_file_that_is_not_a_deck_is_refused(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"version": 99, "kind": "other"}))
    with pytest.raises(ReviewRefused, match="not a"):
        load_deck(p)
