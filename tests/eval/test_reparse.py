"""The re-parse dry run. Its value depends entirely on the identity gate: if the
hint reconstruction drifts from extract.py, `identical` stops covering every
pair and the numbers would silently attribute extract's behaviour to the
parser."""
from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.reparse import _HINTS, reparse_report
from app.eval.score import score_doc
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _pt_box(x, y):
    return (SCALE * (x - 15), SCALE * (y - 5), SCALE * (x + 15), SCALE * (y + 5))


def _case(pred_kwargs, gold_kwargs):
    gold = GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                   characteristics=[GoldCharacteristic(
                       balloon=1, position_pt=(100, 100), **gold_kwargs)])
    dump = PredictionDump(
        doc_id="D", config=RunConfig(model_id="stub", dpi=300), scale=SCALE,
        page_rect=RECT, result=ExtractionResult(characteristics=[
            Characteristic(pos=1, target_region=_pt_box(100, 100),
                           **pred_kwargs)]))
    score = score_doc(dump, gold, ReviewCostWeights(), MatchParams())
    return reparse_report({"D": dump}, {"D": gold}, [score])


def test_unmodified_parser_reproduces_every_stored_field():
    """The gate. Stored fields came from parse_value at predict time, so
    re-parsing must reproduce them exactly -- otherwise the hint reconstruction
    is wrong and every other number here is measuring that instead."""
    r = _case(dict(char_type="Diameter", nominal="20", upper_tol="0,1",
                   lower_tol="-0,1", raw_text="Ø20 +0,1 -0,1"),
              dict(char_type="Diameter", nominal="20", upper_tol="0,1",
                   lower_tol="-0,1"))
    assert r["n_pairs"] == 1
    assert r["identical"] == r["n_pairs"]
    assert r["would_fix"] == 0 and r["would_break"] == 0


def test_would_fix_counts_a_row_a_better_parse_would_correct():
    """A stored parse that lost the value the raw text plainly contains: this is
    the bucket a candidate parser change is trying to grow."""
    r = _case(dict(char_type="Diameter", nominal="2", raw_text="Ø20 +0,1 -0,1"),
              dict(char_type="Diameter", nominal="20", upper_tol="0,1",
                   lower_tol="-0,1"))
    assert r["would_fix"] == 1
    assert r["identical"] == 0


def test_would_break_counts_a_row_the_reparse_makes_wrong():
    r = _case(dict(char_type="Distance", nominal="20", raw_text="totally other"),
              dict(char_type="Distance", nominal="20"))
    assert r["would_break"] == 1


def test_hint_map_matches_extract_so_the_coupling_cannot_drift_silently():
    from app.pipeline.extract import _HINTS as pipeline_hints
    assert _HINTS == pipeline_hints


def test_report_is_values_blind():
    import json
    r = _case(dict(char_type="Diameter", nominal="2", raw_text="Ø20 +0,1 -0,1"),
              dict(char_type="Diameter", nominal="20"))
    blob = json.dumps(r, ensure_ascii=False)
    for leak in ("Ø20", "0,1", "'20'", "raw_text"):
        assert leak not in blob, f"reparse report leaked {leak!r}"


# --- was a gdt row's char_type READ, or did the parser default it? -----------
#
# `parser._gdt_type` returns Flatness when it recognises no GD&T symbol in the
# text. So a gdt row predicted as Flatness is either a reading (the frame's
# symbol was transcribed and mapped) or a guess (nothing was recognised). For
# the rows wrong in char_type ONLY, that split says whether the fix is in the
# parser/read or in the scorer's vocabulary -- and it is countable from the
# stored raw_text without a single value leaving the dump.

_GDT_ROW = dict(kind="gdt", char_type="Flatness", nominal="0",
                upper_tol="0,05", lower_tol="0")
_GDT_GOLD = dict(char_type="Position", nominal="0", upper_tol="0,05",
                 lower_tol="0")


def test_a_gdt_row_with_no_recognised_symbol_counts_as_parser_defaulted():
    r = _case(dict(_GDT_ROW, raw_text="0,05 A"), _GDT_GOLD)
    gdt = r["gdt_char_type_only"]
    assert (gdt["n"], gdt["symbol_recognised"], gdt["parser_defaulted"]) == (1, 0, 1)


def test_a_gdt_row_whose_symbol_was_read_counts_as_recognised():
    r = _case(dict(_GDT_ROW, raw_text="⏥ 0,05 A"), _GDT_GOLD)
    gdt = r["gdt_char_type_only"]
    assert (gdt["n"], gdt["symbol_recognised"], gdt["parser_defaulted"]) == (1, 1, 0)


def test_only_char_type_only_gdt_rows_are_counted():
    """A row with a second fault cannot be freed by a char_type fix, and a
    dimension row has no GD&T symbol to recognise."""
    wrong_twice = _case(dict(_GDT_ROW, raw_text="0,05 A", upper_tol="9"),
                        _GDT_GOLD)
    assert wrong_twice["gdt_char_type_only"]["n"] == 0
    dim = _case(dict(char_type="Distance", nominal="20", raw_text="20"),
                dict(char_type="Diameter", nominal="20"))
    assert dim["gdt_char_type_only"]["n"] == 0


# --- the closed-vocabulary probe over those rows ------------------------------

_PROFILE_GOLD = dict(char_type="Linienform Profil zu A", nominal="0",
                     upper_tol="0,05", lower_tol="0")


def test_the_probe_reports_listed_glyphs_and_label_words_per_row():
    """Label words are split by how the scorer reads the label today, which
    isolates the rows it finds no type word in -- where a synonym belongs."""
    r = _case(dict(_GDT_ROW, raw_text="⌒ 0,05 A"), _PROFILE_GOLD)
    probe = r["gdt_char_type_only"]["probe"]
    assert probe["transcription_glyphs"] == {"U+2312 ARC": 1}
    assert probe["transcription_words"] == {"(none listed)": 1}
    assert probe["label_words_by_scorer"] == {"none": {"linienform+profil": 1}}
    assert probe["unlisted_symbol_rows"] == 0


def test_an_unlisted_symbol_is_counted_so_a_miss_is_visible():
    r = _case(dict(_GDT_ROW, raw_text="★ 0,05 A"), _PROFILE_GOLD)
    probe = r["gdt_char_type_only"]["probe"]
    assert probe["transcription_glyphs"] == {"(none listed)": 1}
    assert probe["unlisted_symbol_rows"] == 1


def test_the_probe_never_carries_text_outside_its_lists():
    import json
    r = _case(dict(_GDT_ROW, raw_text="⌒ 0,05 SECRETREAD"),
              dict(_PROFILE_GOLD, char_type="Linienform Profil SECRETLABEL"))
    blob = json.dumps(r, ensure_ascii=False)
    assert "SECRET" not in blob and "0,05" not in blob


def test_the_probe_reports_what_stands_before_the_value():
    r = _case(dict(_GDT_ROW, raw_text="Xy 0,05 A"), _PROFILE_GOLD)
    assert r["gdt_char_type_only"]["probe"]["transcription_prefix"] == {
        "Lu · Ll": 1}


def test_the_probe_keys_each_row_by_the_review_decks_id():
    """So each row's closed-vocabulary signature can be read beside the
    operator's answer for the same row in the tally -- which glyph goes with
    which characteristic is exactly what a parser mapping needs. The ids must
    be the deck's, or the join is silently wrong."""
    from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                                 PredictionDump, ReviewCostWeights, RunConfig)
    from app.eval.review import collect_gdt_rows
    from app.models import Characteristic, ExtractionResult
    dumps, golds, scores = {}, {}, []
    for doc_id, xs in (("P2", [(100, 700, "Π 0,05 A")]),
                       ("P1", [(900, 100, "0,05 A"), (100, 100, "Ø0,1 A")])):
        gold = GoldDoc(doc_id=doc_id, pdf="d", excel="e", page_rect=RECT,
                       characteristics=[GoldCharacteristic(
                           balloon=i + 1, position_pt=(x, y), **_PROFILE_GOLD)
                           for i, (x, y, _) in enumerate(xs)])
        dump = PredictionDump(
            doc_id=doc_id, config=RunConfig(model_id="stub", dpi=300),
            scale=SCALE, page_rect=RECT,
            result=ExtractionResult(characteristics=[
                Characteristic(pos=i + 1, target_region=_pt_box(x, y),
                               **dict(_GDT_ROW, raw_text=t))
                for i, (x, y, t) in enumerate(xs)]))
        dumps[doc_id], golds[doc_id] = dump, gold
        scores.append(score_doc(dump, gold, ReviewCostWeights(), MatchParams()))
    rows = reparse_report(dumps, golds, scores)["gdt_char_type_only"]["rows"]
    deck_ids = [r.id for r in collect_gdt_rows(dumps, golds, scores)]
    assert sorted(rows) == sorted(deck_ids) == ["g1", "g2", "g3"]
    assert rows["g1"]["prefix"] == "(no prefix)"                 # P1 balloon 1
    assert rows["g2"]["prefix"].startswith("U+00D8")             # P1 balloon 2
    assert rows["g3"]["prefix"] == "U+03A0 GREEK CAPITAL LETTER PI"  # P2
    assert rows["g3"]["scorer"] == "none"
