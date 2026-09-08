"""Confidence, recovered from vLLM logprobs instead of transformers scores.

Route B serves `read-lora-v1` on Qwen's official AWQ checkpoint through vLLM,
which returns logprobs rather than the raw `output_scores` the transformers
backend softmaxes. The number both paths must produce is the same one:
`review.LOW_CONF = 0.8` decides which rows are flagged, it is worth 3.00 review
cost on its own, and 284 of 308 matched pairs sit at >= 0.8 — so a confidence
scale that is off even slightly reclassifies rows wholesale and the vLLM arm
would measure the flagging change rather than the adapter.

Greedy decoding is what makes the two definitions coincide: the sampled token IS
the argmax, so exp(chosen logprob) is the max softmax probability that
`_mean_token_confidence` averages.
"""
import math

import pytest

from app.pipeline.ocr.vllm_backend import mean_confidence_from_logprobs


class _Logprob:
    """vLLM hands back {token_id: Logprob(logprob=...)} per generated token."""

    def __init__(self, logprob):
        self.logprob = logprob


def _step(*logprobs):
    return {i: _Logprob(lp) for i, lp in enumerate(logprobs)}


def test_a_confident_decode_scores_high():
    steps = [_step(math.log(0.99)), _step(math.log(0.98)), _step(math.log(0.97))]
    assert mean_confidence_from_logprobs(steps) > 0.9


def test_an_uncertain_decode_scores_low():
    steps = [_step(math.log(0.4)), _step(math.log(0.5)), _step(math.log(0.3))]
    assert mean_confidence_from_logprobs(steps) < 0.6


def test_an_empty_decode_is_zero():
    """Matches _mean_token_confidence: an empty read is not a confident one."""
    assert mean_confidence_from_logprobs([]) == 0.0


def test_the_most_likely_candidate_is_the_one_that_counts():
    """vLLM may return several candidates per step. The transformers path
    averages the MAX softmax probability, so this must take the max too, not the
    first key or the mean of the candidates."""
    steps = [_step(math.log(0.1), math.log(0.85), math.log(0.05))]
    assert mean_confidence_from_logprobs(steps) == pytest.approx(0.85)


def test_it_agrees_with_the_transformers_path_on_the_same_probabilities():
    """The comparability pin. Both backends must put a row on the same side of
    LOW_CONF=0.8, or the vLLM arm measures a flagging change rather than the
    adapter."""
    from app.pipeline.ocr.vlm_backend import _mean_token_confidence

    probs = [0.95, 0.72, 0.88, 0.99]
    theirs = _mean_token_confidence(probs)
    ours = mean_confidence_from_logprobs([_step(math.log(p)) for p in probs])

    assert ours == pytest.approx(theirs)


def test_missing_logprobs_fail_loudly_instead_of_scoring_zero():
    """vLLM returns None when logprobs were not requested. Treating that as 0.0
    would flag EVERY row -- 0.0 is below any threshold -- which reads as a
    catastrophically unconfident model rather than as a misconfigured request,
    and it would silently rewrite the review cost the arm is measuring."""
    with pytest.raises(ValueError, match="logprobs"):
        mean_confidence_from_logprobs(None)


# --- the read/detect scoping, as a per-request adapter choice ----------------

from app.pipeline.ocr.vllm_backend import adapter_for_pass


def test_detection_gets_no_adapter():
    """The whole reason Rung 3's second arm was readable. read-lora-v1 saw only
    callout read crops in training; serving it over detection is what took
    false_detection from 607 to 931 and cost the first arm +10.00."""
    assert adapter_for_pass("detect", "read-lora-v1") is None


def test_every_read_pass_gets_the_adapter():
    """Disabling it everywhere would serve the base model under a treatment
    arm's run name -- the failure resolve_adapter already refuses."""
    for pass_name in ("read", "gdt", "notes", "title"):
        assert adapter_for_pass(pass_name, "read-lora-v1") == "read-lora-v1", pass_name


def test_a_base_run_carries_no_adapter_on_any_pass():
    """The control. With no adapter selected nothing may attach one."""
    for pass_name in ("read", "gdt", "notes", "title", "detect"):
        assert adapter_for_pass(pass_name, None) is None, pass_name


def test_an_unclassified_pass_fails_loudly():
    """A pass added later must not silently inherit either answer. Defaulting to
    "no adapter" quietly turns a treatment arm into a control for that pass;
    defaulting to "adapter" repeats the detection confound. Neither is
    detectable in the output, so refuse instead."""
    with pytest.raises(ValueError, match="unclassified"):
        adapter_for_pass("marks", "read-lora-v1")


# --- sampling: determinism is the comparison method, not a preference --------

from app.pipeline.ocr.vllm_backend import sampling_kwargs


def test_decoding_is_greedy():
    """CLAUDE.md section 5: scoring is deterministic here BECAUSE decoding is
    greedy — 16 unchanged documents gave per-document deltas of exactly 0.0
    across a GPU change, which is what licenses one arm per hypothesis and makes
    any non-zero delta causal. Sampling would destroy that, and every conclusion
    in the repo rests on it."""
    kw = sampling_kwargs(max_tokens=40)
    assert kw["temperature"] == 0.0


def test_logprobs_are_requested():
    """Without them vLLM returns None and mean_confidence_from_logprobs raises —
    by design, since scoring None as 0.0 would flag every row."""
    assert sampling_kwargs(max_tokens=40)["logprobs"] >= 1


def test_the_token_budget_is_carried_through():
    """The four passes have different budgets: 40 for a callout, 512 for the
    notes block, 128 for the title, 1024 for detection. A single default would
    truncate the notes block, which is a silent content loss."""
    assert sampling_kwargs(max_tokens=1024)["max_tokens"] == 1024
