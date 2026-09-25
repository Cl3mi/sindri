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


# --- answers and the tally ----------------------------------------------------

from app.eval.review import (answers_path, check_tally_out,  # noqa: E402
                             load_answers, save_answer, tally)


def _deck3():
    return build_deck(_rows3(), run="r", originals_dir="/o", stamped_dir="/s")


def _a(drawing, symbol, gold):
    return {"drawing_shows": drawing, "symbol_in_transcription": symbol,
            "gold_label_means_it": gold}


def test_nothing_answered_tallies_as_incomplete():
    t = tally(_deck3(), {})
    assert t["n_rows"] == 3 and t["answered"] == 0
    assert t["incomplete"] == ["g1", "g2", "g3"]


def test_the_tally_routes_each_answered_row_to_where_its_fix_lives():
    """The decision the review exists for. A row is freed WITHOUT A GPU only
    when the prediction side is a parser fix (the symbol IS in the
    transcription, the parser just does not map it) AND the scorer already
    reads gold's label as the same characteristic. A dropped symbol is a read-
    stage fault; a gold label that disagrees with the drawing is a data fault."""
    t = tally(_deck3(), {"g1": _a("Position", "glyph", "yes"),
                         "g2": _a("Position", "none", "yes"),
                         "g3": _a("Parallelism", "glyph", "no")})
    assert t["answered"] == 3 and t["incomplete"] == [] and t["invalid"] == []
    assert t["drawing_shows"] == {"Position": 2, "Parallelism": 1}
    assert t["prediction_fix"] == {"parser": 2, "read_stage": 1}
    assert t["gold_side"] == {"scorer_already_matches": 2,
                              "gold_disagrees_with_drawing": 1}
    assert t["freed_without_gpu"] == 1


def test_a_label_the_scorer_cannot_read_needs_a_synonym_word():
    """5 of the 8 real rows are `unmapped(none)`. If the operator says the label
    DOES name the characteristic, the fix is a synonym-map word -- still CPU-
    only, so the row is freed without a GPU when the parser fix applies too."""
    rows = _collect(_doc("P1", [
        (100, 100, dict(_GDT, raw_text="0,05 A"),
         dict(GOLD_POSITION, char_type="Lagetoleranz zu A"))]))
    deck = build_deck(rows, "r", "/o", "/s")
    assert deck["rows"][0]["scorer_reads_gold_as"] == "none"
    t = tally(deck, {"g1": _a("Position", "word", "yes")})
    assert t["gold_side"] == {"needs_synonym_word": 1}
    assert t["freed_without_gpu"] == 1


def test_a_hand_edited_answer_outside_the_vocabulary_is_invalid():
    t = tally(_deck3(), {"g1": _a("Positon", "glyph", "yes")})
    assert t["invalid"] == ["g1"] and t["answered"] == 0


def test_the_tally_is_values_blind_whatever_the_answers_file_holds():
    """The tally is what reaches an agent, so nothing free-form passes through
    it: not the operator's note, not the label or transcription, not the part
    number."""
    rows = _collect(_doc("PARTNO-4711", [
        (100, 100, dict(_GDT, raw_text="0,05 SECRET-TEXT"),
         dict(GOLD_POSITION, char_type="Position SECRET-LABEL"))]))
    deck = build_deck(rows, "r", "/o", "/s")
    answers = {"g1": dict(_a("Position", "glyph", "yes"), note="SECRET-NOTE")}
    blob = json.dumps(tally(deck, answers), ensure_ascii=False)
    for leak in ("SECRET", "PARTNO", "4711", "0,05"):
        assert leak not in blob, leak


def _saved(tmp_path, monkeypatch):
    root = tmp_path / "client"
    root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    write_deck(root / "d.json", _deck3())
    return root / "d.json", load_deck(root / "d.json")


def test_answers_persist_merge_and_canonicalise(tmp_path, monkeypatch):
    """One POST per click: each save merges into the row, and capitals or extra
    spaces never create a second spelling of the same answer."""
    path, deck = _saved(tmp_path, monkeypatch)
    save_answer(path, deck, "g1", {"drawing_shows": "total  RUNOUT"})
    save_answer(path, deck, "g1", {"symbol_in_transcription": "Glyph",
                                   "note": "mine"})
    assert load_answers(path, deck)["g1"] == {
        "drawing_shows": "Total runout", "symbol_in_transcription": "glyph",
        "note": "mine"}
    assert answers_path(path).name == "d.answers.json"


def test_a_blank_value_clears_an_answer(tmp_path, monkeypatch):
    """Clicking the selected option again un-answers it -- it must not stick."""
    path, deck = _saved(tmp_path, monkeypatch)
    save_answer(path, deck, "g1", {"drawing_shows": "Position"})
    save_answer(path, deck, "g1", {"drawing_shows": ""})
    assert load_answers(path, deck)["g1"]["drawing_shows"] == ""


def test_save_refuses_unknown_rows_fields_and_values(tmp_path, monkeypatch):
    path, deck = _saved(tmp_path, monkeypatch)
    for rid, ans in (("g9", {"drawing_shows": "Position"}),
                     ("g1", {"colour": "red"}),
                     ("g1", {"drawing_shows": "Positon"}),
                     ("g1", {"note": "x" * 2001})):
        with pytest.raises(ReviewRefused):
            save_answer(path, deck, rid, ans)
    assert load_answers(path, deck) == {}


def test_answers_made_against_another_deck_are_refused(tmp_path, monkeypatch):
    """A regenerated deck may number different callouts g1..g8; reading old
    answers against it would attribute them to the wrong rows."""
    path, deck = _saved(tmp_path, monkeypatch)
    save_answer(path, deck, "g1", {"drawing_shows": "Position"})
    with pytest.raises(ReviewRefused, match="different deck"):
        load_answers(path, dict(deck, run="another"))


def test_the_tally_may_not_be_written_into_a_protected_root(tmp_path,
                                                            monkeypatch):
    """It is the one output meant for an agent; inside a root no agent can
    read it, so writing it there would silently lose the review."""
    root = tmp_path / "client"
    root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    with pytest.raises(ReviewRefused):
        check_tally_out(root / "tally.json")
    assert check_tally_out(tmp_path / "docs" / "tally.json")


# --- the three entry points ---------------------------------------------------

from app.eval.runner import main  # noqa: E402
from tests.eval.test_score_scope_policy import _corpus  # noqa: E402


def _score_args(tmp_path, run_dir, gold_dir, pdfs, deck):
    return ["score", "--run", str(run_dir), "--gold", str(gold_dir),
            "--pdfs", str(pdfs), "--name", "ws",
            "--out", str(tmp_path / "scored.json"), "--review-deck", str(deck)]


def test_score_refuses_a_deck_outside_a_protected_root_before_scoring(
        tmp_path, monkeypatch):
    """Fail in seconds, not after a full re-score, and write nothing either:
    a refusal that still did the work would teach the operator to ignore it."""
    _roots(tmp_path, monkeypatch, tmp_path / "client")
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    assert main(_score_args(tmp_path, run_dir, gold_dir, pdfs,
                            tmp_path / "d.json")) == 1
    assert not (tmp_path / "d.json").exists()
    assert not (tmp_path / "scored.json").exists()


def test_score_needs_pdfs_to_know_where_the_drawings_are(tmp_path,
                                                         monkeypatch):
    root = tmp_path / "client"
    root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    args = _score_args(tmp_path, run_dir, gold_dir, pdfs, root / "d.json")
    i = args.index("--pdfs")
    del args[i:i + 2]
    assert main(args) == 1
    assert not (root / "d.json").exists()


def test_score_writes_the_deck_and_prints_only_a_count(tmp_path, monkeypatch,
                                                       capsys):
    root = tmp_path / "client"
    root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    deck = root / "reports" / "d.json"
    assert main(_score_args(tmp_path, run_dir, gold_dir, pdfs, deck)) == 0
    d = load_deck(deck)
    assert d["originals_dir"] == str(pdfs)
    assert d["stamped_dir"] == str(pdfs.parent / "stamped")
    out = capsys.readouterr().out
    assert "review deck:" in out and '"rows"' not in out


def test_review_tally_reads_the_answers_beside_the_deck(tmp_path, monkeypatch):
    path, deck = _saved(tmp_path, monkeypatch)
    save_answer(path, deck, "g1", _a("Position", "glyph", "yes"))
    out = tmp_path / "docs" / "tally.json"
    assert main(["review-tally", str(path), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["freed_without_gpu"] == 1


def test_review_serve_refuses_a_tally_path_inside_a_root(tmp_path, monkeypatch):
    path, _ = _saved(tmp_path, monkeypatch)
    assert main(["review-serve", str(path),
                 "--tally-out", str(path.parent / "t.json")]) == 1
