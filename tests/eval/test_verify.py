"""runner verify: asks the verifier about exactly the rows today's code would
ship unflagged, and writes the ORIGINAL dump plus those verdicts
(docs/plans/2026-10-07-verifier-registration.md §3)."""
import pytest
from PIL import Image

from app.eval import verify as vm
from app.eval.models import PredictionDump, RunConfig
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1000.0, 1000.0)


class StubVerifier:
    def __init__(self, answer=0.2):
        self.answer = answer
        self.calls = []

    def verify_region(self, image):
        self.calls.append(image.size)
        return self.answer


def _dump():
    def c(pos, **kw):
        base = dict(pos=pos, kind="dimension", char_type="Distance",
                    nominal="20", upper_tol="0,1", lower_tol="-0,1",
                    raw_text="20 ±0,1", confidence=0.995,
                    target_region=(100.0 * pos, 100.0, 100.0 * pos + 80, 140.0))
        base.update(kw)
        return Characteristic(**base)
    chars = [
        c(1),                                     # deliverable
        c(2, raw_text="20", upper_tol="", lower_tol=""),   # no_tolerance flags it
        c(3, confidence=0.5),                     # low confidence flags it
        c(4, kind="gdt", raw_text="0,05"),        # nondim_kind flags it
        c(5, confidence=0.95),                    # stage-2 drops it (conf < 0.99)
        c(6),                                     # deliverable
    ]
    return PredictionDump(doc_id="D", config=RunConfig(model_id="s", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))


def _page():
    return Image.new("RGB", (4200, 4200), "white")


def test_only_deliverable_rows_are_asked():
    stub = StubVerifier()
    out, stats = vm.verify_dump(_dump(), _page(), SCALE, stub)
    asked = {c.pos for c in out.result.characteristics if c.verifier_p is not None}
    assert asked == {1, 6}
    assert stats == {"rows": 6, "asked": 2, "no_answer": 0}
    assert len(stub.calls) == 2


def test_the_output_is_the_original_dump_plus_verdicts():
    """Stored, not reapplied: the dumps must stay what the GPU produced, so
    scoring them later still runs today's code exactly once."""
    original = _dump()
    out, _ = vm.verify_dump(original, _page(), SCALE, StubVerifier(0.8))
    assert len(out.result.characteristics) == 6        # nothing dropped
    for a, b in zip(original.result.characteristics, out.result.characteristics):
        assert a.model_dump(exclude={"verifier_p"}) == \
            b.model_dump(exclude={"verifier_p"})


def test_the_prompt_hash_is_recorded():
    from app.pipeline.verifier import verifier_prompt_hash
    out, _ = vm.verify_dump(_dump(), _page(), SCALE, StubVerifier())
    assert out.config.extra["verifier_prompt"] == verifier_prompt_hash()


def test_no_answer_is_counted_and_left_none():
    out, stats = vm.verify_dump(_dump(), _page(), SCALE, StubVerifier(None))
    assert stats["no_answer"] == 2
    assert all(c.verifier_p is None for c in out.result.characteristics)


def test_input_dump_is_not_mutated():
    d = _dump()
    before = d.model_dump_json()
    vm.verify_dump(d, _page(), SCALE, StubVerifier())
    assert d.model_dump_json() == before


def test_a_page_rendered_at_another_scale_is_refused():
    """Boxes are in render pixels of the ORIGINAL render; a page rendered at
    any other scale would crop the wrong place, and every verdict would be
    about the wrong region while looking perfectly normal."""
    with pytest.raises(vm.ScaleMismatch):
        vm.verify_dump(_dump(), _page(), SCALE * 0.5, StubVerifier())
