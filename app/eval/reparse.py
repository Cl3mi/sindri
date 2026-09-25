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
from app.eval import vocab_probe
from app.eval.report import is_char_type_only, scorer_side
from app.pipeline.parser import _GDT_SYMBOLS, parse_value

# Copy of extract._HINTS: detector kind -> parser hint. Duplicated so this
# module never imports extract (which pulls in render/detect/ocr); the equality
# test in tests/eval/test_reparse.py is what stops the copy from drifting.
_HINTS = {"material": "material", "note": "note", "gdt": "gdt",
          "theoretical": "theoretical"}

_FIELDS = ("nominal", "upper_tol", "lower_tol")


def gdt_symbol_recognised(raw_text: str) -> bool:
    """Whether `parser._gdt_type` would find a GD&T symbol in this text. When it
    does not, it returns Flatness anyway -- so a gdt char_type is either READ
    or GUESSED, and this is the one test that tells them apart. Shared with
    app.eval.review so the worksheet and the count agree row for row."""
    text = (raw_text or "").replace("\n", " ")
    return any(sym in text for sym in _GDT_SYMBOLS)


def _matches_gold(c, gold) -> bool:
    """Same verdict score._compare_fields reaches, expressed as a bool."""
    if gold.char_type and not char_type_equal(c.char_type, gold.char_type):
        return False
    return all(values_equal(getattr(c, f), getattr(gold, f)) for f in _FIELDS)


def _same_parse(a, b) -> bool:
    return (a.char_type == b.char_type
            and all(getattr(a, f) == getattr(b, f) for f in _FIELDS))


def _bump(d: Dict, k: str) -> None:
    d[k] = d.get(k, 0) + 1


def _probe_row(probe: Dict, transcription: str, label: str, side: str) -> None:
    _bump(probe["transcription_glyphs"],
          vocab_probe.signature(vocab_probe.glyph_set(transcription)))
    _bump(probe["transcription_words"], vocab_probe.signature(
        vocab_probe.word_set(transcription, vocab_probe.TRANSCRIPTION_WORDS)))
    _bump(probe["label_words_by_scorer"].setdefault(side, {}),
          vocab_probe.signature(
              vocab_probe.word_set(label, vocab_probe.LABEL_WORDS)))
    probe["unlisted_symbol_rows"] += int(
        vocab_probe.has_unlisted_symbol(transcription))


def reparse_report(dumps: Dict, golds: Dict, scores: List) -> Dict:
    """Counts only — never a value — over every matched pair in `scores`.

    would_fix / would_break are the two directions that matter: a parser change
    is worth shipping when it flips wrong rows to right without flipping right
    rows to wrong, and the second number is the one a cost-only reading of the
    first would miss."""
    out = {"n_pairs": 0, "identical": 0, "would_fix": 0, "would_break": 0,
           "still_wrong": 0, "still_correct": 0}
    # parser._gdt_type falls back to Flatness when it recognises no symbol, so
    # a gdt char_type is either READ or GUESSED. On the rows wrong in char_type
    # alone that split routes the fix: a guessed type points at the read or the
    # parser, a read one that still disagrees points at the scorer's vocabulary.
    gdt = {"n": 0, "symbol_recognised": 0, "parser_defaulted": 0}
    # Closed-vocabulary probe (vocab_probe): which LISTED glyphs and ISO 1101
    # words occur, as per-row sets, so co-occurrence survives. Label words are
    # split by how the scorer reads the label today. Nothing outside the lists
    # can reach this output; unlisted symbols are only counted.
    probe = {"transcription_glyphs": {}, "transcription_words": {},
             "label_words_by_scorer": {}, "unlisted_symbol_rows": 0}
    gdt["probe"] = probe
    out["gdt_char_type_only"] = gdt
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
            if p.kind == "gdt" and is_char_type_only(pair):
                gdt["n"] += 1
                if gdt_symbol_recognised(p.raw_text):
                    gdt["symbol_recognised"] += 1
                else:
                    gdt["parser_defaulted"] += 1
                _probe_row(probe, p.raw_text or "", g.char_type or "",
                           scorer_side(pair))
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
