"""reapply_current_code re-derives what today's parser, today's review flags,
and today's ACTIVE policy rules would have produced from a stored dump's
raw_text -- the reconstruction score --reapply-policy relies on."""
from app.eval.models import PredictionDump
from app.eval.reapply import reapply_current_code
from app.eval.models import RunConfig
from app.models import Characteristic, ExtractionResult, Note, NoteBlock
from app.pipeline.parser import parse_value

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _dump(chars, notes=None):
    return PredictionDump(doc_id="D", config=RunConfig(model_id="s", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars,
                                                  notes=notes))


def test_input_dump_is_not_mutated():
    c = Characteristic(pos=1, kind="dimension", char_type="Distance",
                       nominal="20", raw_text="20", confidence=0.99)
    dump = _dump([c])
    before = dump.model_dump_json()
    reapply_current_code(dump)
    assert dump.model_dump_json() == before


def test_a_stale_char_type_gets_todays_parse():
    """A gdt row whose text opens with a bare diameter sign and carries no
    recognised GD&T symbol: parser._gdt_type now infers Position for that
    shape (2026-09-25 fix), where the stored dump -- predicted before the
    fix -- still holds the old Flatness default."""
    raw = "Ø 0,05"
    # The gate: confirm today's parser actually disagrees with the stored
    # value, so this test would fail loudly if the fixture stopped exercising
    # the fix it is meant to probe.
    assert parse_value(raw, hint="gdt").char_type == "Position"
    c = Characteristic(pos=1, kind="gdt", char_type="Flatness", nominal="0",
                       upper_tol="0,05", lower_tol="0", raw_text=raw,
                       confidence=0.99)
    dump = _dump([c])
    out = reapply_current_code(dump)
    new = out.result.characteristics[0]
    assert new.char_type == "Position"
    assert new.pos == 1 and new.kind == "gdt" and new.confidence == 0.99


def test_rotation_ambiguity_survives_reapplication():
    c = Characteristic(pos=1, kind="dimension", char_type="Distance",
                       nominal="20", raw_text="20", confidence=0.99,
                       needs_review=True, review_reasons=["rotation ambiguity"])
    dump = _dump([c])
    out = reapply_current_code(dump)
    new = out.result.characteristics[0]
    assert new.needs_review is True
    assert "rotation ambiguity" in new.review_reasons


def test_identity_when_no_rules_are_active_and_fields_already_match_todays_parse(
        monkeypatch):
    """Built so every stored field is exactly what parse_value + review_flags
    produce for this raw_text today -- the gate for the runner-level identity
    test, isolated to reapply_current_code alone. ACTIVE_*_RULES are pinned to
    empty here because THIS test is about the reparse+reflag reconstruction,
    not about whatever rules happen to be shipped today."""
    from app.pipeline import policy_rules as pr
    monkeypatch.setattr(pr, "ACTIVE_FLAG_RULES", ())
    monkeypatch.setattr(pr, "ACTIVE_DROP_STAGES", ())
    c = Characteristic(pos=1, id="abc", kind="dimension", char_type="Distance",
                       nominal="20", raw_text="20", confidence=0.99,
                       needs_review=False, review_reasons=[])
    dump = _dump([c])
    out = reapply_current_code(dump)
    assert out.result.characteristics[0].model_dump() == c.model_dump()


def test_known_note_positions_feed_the_note_ref_check():
    top = Note(pos=101, text_en="x")
    ref = Characteristic(pos=1, kind="note", subtype="note_ref",
                         raw_text="999", note_ref_pos=999, confidence=0.99)
    dump = _dump([ref], notes=NoteBlock(region=RECT, notes=[top]))
    out = reapply_current_code(dump)
    new = out.result.characteristics[0]
    assert "unknown note reference" in new.review_reasons


# --- the general-tolerance arm (docs/plans/2026-10-05-general-tolerance-registration.md)

def _gentol_dump(chars):
    from app.models import TitleField
    d = _dump(chars)
    d.result.title_block = [TitleField(label="Tol", value="ISO 2768-mK")]
    return d


def test_fill_off_is_todays_reapply_exactly():
    """The invariant the registration pins: with the fill disabled the
    reordered path (drops -> fill -> flags) emits byte-identical rows."""
    c = Characteristic(pos=1, kind="dimension", char_type="Distance",
                       nominal="20", raw_text="20", confidence=0.99)
    d = _gentol_dump([c])
    assert (reapply_current_code(d, fill=False).model_dump_json()
            == reapply_current_code(d).model_dump_json())


def test_fill_on_fills_and_the_no_tolerance_flag_no_longer_fires():
    c = Characteristic(pos=1, kind="dimension", char_type="Distance",
                       nominal="20", raw_text="20", confidence=0.99)
    d = _gentol_dump([c])
    off = reapply_current_code(d, fill=False).result.characteristics[0]
    on = reapply_current_code(d, fill=True).result.characteristics[0]
    assert off.needs_review and not off.upper_tol
    assert (on.upper_tol, on.lower_tol) == ("0,2", "-0,2")
    assert not on.needs_review and on.review_reasons == []


def test_fill_on_keeps_other_flags():
    c = Characteristic(pos=1, kind="dimension", char_type="Distance",
                       nominal="20", raw_text="20", confidence=0.5)
    on = reapply_current_code(_gentol_dump([c]), fill=True)
    new = on.result.characteristics[0]
    assert new.upper_tol == "0,2"
    assert new.needs_review and new.review_reasons == ["low OCR confidence"]


def test_fill_runs_after_drops_so_a_dropped_row_is_never_filled():
    """contained_duplicate drops a box strictly inside a larger, related one
    at no higher confidence. The survivor is the outer box, and only it may
    be filled -- filling before dropping would have filled the dropped one
    too, harmlessly here, but a fill must never be what decides a drop."""
    from app.pipeline import policy_rules as pr
    outer = Characteristic(pos=1, kind="dimension", char_type="Distance",
                           nominal="20", raw_text="20", confidence=0.99,
                           target_region=(100.0, 100.0, 300.0, 160.0))
    inner = outer.model_copy(update={"pos": 2, "confidence": 0.9,
                                     "target_region": (120.0, 110.0,
                                                       200.0, 150.0)})
    # the gate: the fixture must actually trip the active drop rule
    assert [c.pos for c in pr.apply_drop_stages([outer, inner],
                                                pr.ACTIVE_DROP_STAGES)] == [1]
    on = reapply_current_code(_gentol_dump([outer, inner]), fill=True)
    assert [c.pos for c in on.result.characteristics] == [1]
    assert on.result.characteristics[0].upper_tol == "0,2"


def test_a_verifier_verdict_survives_reapplication():
    """Verdicts are produced on the GPU host and written into the dumps;
    re-scoring re-parses every row, and a field it does not restore is
    silently erased -- the verdicts would vanish before they were priced."""
    c = Characteristic(pos=1, kind="dimension", char_type="Distance",
                       nominal="20", upper_tol="0,1", lower_tol="-0,1",
                       raw_text="20 ±0,1", confidence=0.995, verifier_p=0.31)
    out = reapply_current_code(_dump([c]))
    assert out.result.characteristics[0].verifier_p == 0.31
