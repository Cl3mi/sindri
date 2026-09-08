"""Serving `read-lora-v1` on Qwen's official AWQ checkpoint, through vLLM.

Route B of `docs/plans/2026-09-08-deployment-routes.md`. PEFT cannot attach a
LoRA to an AWQ checkpoint (autoawq replaces every q/k/v/o projection with
WQLinear_GEMM), and merging into bf16 then re-quantising is route A. vLLM
applies adapters at runtime instead, and its
`Qwen2_5_VLForConditionalGeneration` declares SupportsLoRA, SupportsQuant,
SupportsMultiModal and SupportsMRoPE on one class — verified on the host against
vLLM 0.28.0.

The read/detect scoping that made Rung 3 measurable transfers natively here:
vLLM takes a LoRA per REQUEST, so detection issues requests carrying no adapter
and reads issue them carrying one. That is exactly `VLMBackend._base_weights()`,
without a context manager.

Nothing in this module imports vllm at module scope: the CPU image has no vllm,
and the pure functions below are the part worth testing anywhere.
"""
import math


def mean_confidence_from_logprobs(steps) -> float:
    """Mean per-token top probability of a greedy decode; 0.0 for an empty one.

    The transformers backend averages the max softmax probability per step
    (`vlm_backend._mean_token_confidence`). vLLM reports logprobs instead, so
    this exponentiates the largest candidate at each step to land on the same
    scale. Under greedy decoding the sampled token IS the argmax, so the two
    definitions coincide -- which they must, because `review.LOW_CONF = 0.8`
    decides what gets flagged, is worth 3.00 review cost by itself, and 284 of
    308 matched pairs sit at >= 0.8. A confidence scale off by a little
    reclassifies rows wholesale, and the arm would then be measuring the
    flagging change rather than the adapter.

    Raises on None rather than scoring it 0.0. vLLM returns None when logprobs
    were not requested, and 0.0 is below every threshold, so it would flag every
    row and read as a catastrophically unconfident model instead of a
    misconfigured request."""
    if steps is None:
        raise ValueError(
            "no logprobs returned: SamplingParams(logprobs=...) was not set. "
            "Refusing to score this as 0.0 confidence, which would flag every "
            "row and silently rewrite the review cost being measured.")
    per_step = [math.exp(max(lp.logprob for lp in step.values()))
                for step in steps if step]
    return float(sum(per_step) / len(per_step)) if per_step else 0.0


# Which passes the adapter applies to. read-lora-v1 was trained on callout read
# crops ONLY, so detection must see the base weights: serving it over detection
# is what took false_detection 607 -> 931 and lost Rung 3's first arm by +10.00.
# The read passes keep it, because disabling it everywhere would serve the base
# model under a treatment arm's run name.
#
# An explicit map rather than a default, so a pass added later cannot silently
# inherit either answer -- both mistakes are invisible in the output.
_ADAPTED_PASSES = {"read": True, "gdt": True, "notes": True, "title": True,
                   "detect": False}


def adapter_for_pass(pass_name: str, adapter):
    """The LoRA name this pass should be served with, or None for base weights.

    vLLM takes an adapter per request, so this replaces the context manager the
    transformers backend needs (`VLMBackend._base_weights()`) with a per-call
    choice -- the same scoping, expressed the way this stack expects it."""
    if pass_name not in _ADAPTED_PASSES:
        raise ValueError(
            f"unclassified pass {pass_name!r}: it must be declared in "
            f"_ADAPTED_PASSES as adapted or not. Guessing would either turn a "
            f"treatment arm into a control for this pass, or repeat the "
            f"detection confound that voided Rung 3's first arm, and neither "
            f"is visible in the output. Known: {sorted(_ADAPTED_PASSES)}.")
    return adapter if (adapter and _ADAPTED_PASSES[pass_name]) else None


def sampling_kwargs(max_tokens: int) -> dict:
    """Arguments for vLLM's SamplingParams, matching the transformers path.

    temperature 0.0 is not a preference, it is the comparison method. CLAUDE.md
    section 5 records that 16 unchanged documents gave per-document deltas of
    exactly 0.0 across a GPU device change BECAUSE decoding is greedy -- which
    is what licenses one arm per hypothesis, with no repeats, and makes any
    non-zero delta causal. Sampling here would invalidate every conclusion in
    the repo, not merely add noise.

    logprobs=1 because the confidence a row is flagged on comes from them, and
    omitting them makes mean_confidence_from_logprobs raise rather than quietly
    score 0.0.

    max_tokens is per pass and never defaulted: 40 for a callout, 128 for the
    title cell, 512 for the notes block, 1024 for detection. One shared budget
    would truncate the notes block, and a truncated JSON array parses to fewer
    notes rather than to an error."""
    return {"temperature": 0.0, "max_tokens": max_tokens, "logprobs": 1}
