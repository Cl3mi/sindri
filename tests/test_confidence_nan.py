"""A NaN confidence made a whole run unscoreable, silently.

2026-09-15: `SPLIT=test ./rescore_onepage.sh r3-awqtest` died loading a dump --

    result.title_block.1.confidence
      Input should be a valid number [input_value=None, input_type=NoneType]

-- after the run had cost nine GPU hours. The chain: a read returned a NaN
confidence, pydantic ACCEPTS NaN at construction, `model_dump_json` serialises
NaN as `null`, and reloading that null fails. The dump was write-only, and
nothing said so until someone tried to score it.

The quieter half is worse than the crash. `review.LOW_CONF` is a `<` comparison,
and every comparison against NaN is False, so a NaN-confidence row is never
flagged -- it ships as a silent error, which is the bucket the whole campaign is
trying to shrink.

It surfaced on the test split because that is where splits.py deliberately put
the structurally atypical drawings. Dev never produced one in ten-plus arms.
"""
import math

import pytest

from app.models import Characteristic, TitleField
from app.pipeline.ocr.vlm_backend import _mean_token_confidence
from app.pipeline.ocr.vllm_backend import mean_confidence_from_logprobs


# --- the source: a read must never hand back a non-finite confidence --------

def test_a_nan_probability_scores_zero_not_nan():
    """float16 AWQ can produce a degenerate logits row whose softmax is NaN.
    0.0 is the right answer, not a crash and not NaN: it matches what
    `extract._safe_read` already returns for a failed read, and it puts the row
    BELOW review.LOW_CONF so a reviewer sees it."""
    assert _mean_token_confidence([0.9, float("nan"), 0.8]) == 0.0


def test_an_infinite_probability_scores_zero_too():
    assert _mean_token_confidence([float("inf")]) == 0.0


def test_ordinary_probabilities_are_untouched():
    """The fix must not move a single existing number: every committed
    measurement in the campaign was scored on this function's output."""
    assert _mean_token_confidence([0.5, 1.0]) == 0.75
    assert _mean_token_confidence([]) == 0.0


def test_the_vllm_path_is_guarded_the_same_way():
    """Route B computes confidence from logprobs instead of softmax, and
    review.LOW_CONF decides what gets flagged on both paths. One guarded and one
    not would make the two stacks disagree about which rows are silent."""
    class _LP:
        def __init__(self, v):
            self.logprob = v

    assert mean_confidence_from_logprobs([{0: _LP(float("nan"))}]) == 0.0


# --- the model: a dump must never become unreadable -------------------------

def _required(model):
    """The non-confidence fields each model insists on."""
    return {"pos": 1} if model is Characteristic else {}

@pytest.mark.parametrize("model", [TitleField, Characteristic])
def test_a_non_finite_confidence_is_stored_as_zero(model):
    """Caught at the boundary, so NaN cannot reach the serialiser and come back
    as null. The whole r3-awqtest run was unreadable for want of this."""
    assert model(confidence=float("nan"), **_required(model)).confidence == 0.0
    assert model(confidence=float("-inf"), **_required(model)).confidence == 0.0


@pytest.mark.parametrize("model", [TitleField, Characteristic])
def test_a_null_confidence_from_an_existing_dump_loads(model):
    """The dumps already on disk carry `null`. Refusing them would mean
    re-predicting nine GPU hours to recover a number that is already there, and
    0.0 is what the value MEANT -- an unusable read, flagged for review."""
    obj = model.model_validate({"confidence": None, **_required(model)})
    assert obj.confidence == 0.0


@pytest.mark.parametrize("model", [TitleField, Characteristic])
def test_a_real_confidence_still_round_trips(model):
    obj = model.model_validate_json(
        model(confidence=0.83, **_required(model)).model_dump_json())
    assert obj.confidence == pytest.approx(0.83)


def test_a_flagging_threshold_cannot_be_dodged_by_nan():
    """The reason this matters beyond the crash: `conf < LOW_CONF` is False for
    NaN, so a NaN row skips flagging entirely and ships as a silent error."""
    from app.pipeline.review import LOW_CONF
    assert not (float("nan") < LOW_CONF), "NaN dodges every threshold"
    assert TitleField(confidence=float("nan")).confidence < LOW_CONF
