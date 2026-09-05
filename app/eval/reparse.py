"""Bounded-gain estimate for a parser change, computed from prediction dumps
already on disk — no GPU.

Dumps store `raw_text`, so `app.pipeline.parser.parse_value` can be re-run over
them offline and the result compared against gold. That turns "would this parser
change help?" from a 9 h GPU arm into a CPU second, which is the whole reason
the 52 misparse rows are worth looking at at all.

This is the ONE place the eval package reaches into the pipeline on purpose, and
it imports only `parser` — a stdlib-and-pydantic module — so the CPU-only score
path stays free of the model stack. The hint mapping is duplicated rather than
imported from `extract` for the same reason; the equality test in
tests/eval/test_reparse.py fails if the two diverge, and the `identical` count
below fails if the reconstruction is wrong for any other reason.

Read `identical == n_pairs` as the gate: with an UNMODIFIED parser every stored
field must be reproducible, because that is where the stored fields came from.
Once a candidate parser edit is in place `identical` drops by design, and
`would_fix - would_break` is the bound on what that edit is worth."""
from typing import Dict, List

from app.eval.normalize import char_type_equal, values_equal
from app.pipeline.parser import parse_value

# Copy of extract._HINTS: detector kind -> parser hint. Duplicated so this
# module never imports extract (which pulls in render/detect/ocr); the equality
# test in tests/eval/test_reparse.py is what stops the copy from drifting.
_HINTS = {"material": "material", "note": "note", "gdt": "gdt",
          "theoretical": "theoretical"}

_FIELDS = ("nominal", "upper_tol", "lower_tol")


def _matches_gold(c, gold) -> bool:
    """Same verdict score._compare_fields reaches, expressed as a bool."""
    if gold.char_type and not char_type_equal(c.char_type, gold.char_type):
        return False
    return all(values_equal(getattr(c, f), getattr(gold, f)) for f in _FIELDS)


def _same_parse(a, b) -> bool:
    return (a.char_type == b.char_type
            and all(getattr(a, f) == getattr(b, f) for f in _FIELDS))


def reparse_report(dumps: Dict, golds: Dict, scores: List) -> Dict[str, int]:
    """Counts only — never a value — over every matched pair in `scores`.

    would_fix / would_break are the two directions that matter: a parser change
    is worth shipping when it flips wrong rows to right without flipping right
    rows to wrong, and the second number is the one a cost-only reading of the
    first would miss."""
    out = {"n_pairs": 0, "identical": 0, "would_fix": 0, "would_break": 0,
           "still_wrong": 0, "still_correct": 0}
    for score in scores:
        dump, gold = dumps[score.doc_id], golds[score.doc_id]
        preds = {c.pos: c for c in dump.result.characteristics}
        gold_by_num = {g.balloon: g for g in gold.characteristics}
        for pair in score.pairs:
            p = preds.get(pair.pred_pos)
            g = gold_by_num.get(pair.gold_balloon)
            if p is None or g is None:
                continue
            out["n_pairs"] += 1
            fresh = parse_value(p.raw_text or "",
                               hint=_HINTS.get(p.kind or "", ""))
            if _same_parse(fresh, p):
                out["identical"] += 1
            was_right = pair.fields_correct
            now_right = _matches_gold(fresh, g)
            if was_right and not now_right:
                out["would_break"] += 1
            elif not was_right and now_right:
                out["would_fix"] += 1
            elif was_right:
                out["still_correct"] += 1
            else:
                out["still_wrong"] += 1
    return out
