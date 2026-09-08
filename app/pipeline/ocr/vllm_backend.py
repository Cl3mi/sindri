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
import os

from PIL import Image

from app.pipeline.ocr.base import OcrResult
# The prompts, the crop cap and the adapter resolution are IMPORTED, never
# copied: runner._prompt_sha256 hashes vlm_backend's five effective prompts
# and every committed measurement carries aa7659f1929184ea, so a second copy
# here could drift while still reporting that hash.
from app.pipeline.ocr.vlm_backend import (
    _DEFAULT_MODEL, _GDT_PROMPT, _NOTES_PROMPT, _TITLE_PROMPT,
    _cap_long_edge, active_adapter, detect_prompt, read_prompt,
    resolve_adapter)


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


# Longest sequence the engine sizes its KV cache for. The checkpoint's config
# advertises max_position_embeddings=128000, and a KV cache for that does not
# fit beside ~40 GB of AWQ weights on one H100. The real ceiling is far lower,
# because nothing here ever sends a whole page: every caller of detect_regions
# passes a tile or a crop (detect.py, notes_block.py, title_block.py). At 14 px
# patches with the 2x2 merge a 1024 px tile is ~1300 visual tokens, a read crop
# capped at 1600 px is ~1600, and the largest completion is detection's 1024 --
# so 8192 is several times the worst case and leaves the rest of the card for
# batching.
_MAX_MODEL_LEN = 8192


class VLLMBackend:
    """The five passes of `VLMBackend`, served by vLLM with a per-request LoRA.

    Same interface, same prompts, same token budgets, same crop cap -- the only
    intended difference from the transformers path is the serving stack, which
    `runner._serving_backend()` records so the two are never mistaken for each
    other in a report."""

    def __init__(self, model_id=None, max_new_tokens: int = 40):
        # Imported lazily for the reason the module docstring gives: the CPU
        # image has no vllm, and app.pipeline.ocr imports this module to decide
        # whether to build it. Same shape as VLMBackend's torch import.
        from vllm import LLM, SamplingParams
        from vllm.lora.request import LoRARequest

        # Held as an attribute rather than imported at module scope, for the
        # same reason `VLMBackend` holds `self.torch`.
        self.sampling_params_cls = SamplingParams
        self.max_new_tokens = max_new_tokens
        # Resolved BEFORE the engine is built: resolve_adapter refuses an
        # unknown name, and finding that out after a ~10 min 72B load wastes
        # the load.
        self.adapter = active_adapter()
        adapter_path = resolve_adapter()
        model_id = model_id or os.getenv("VLM_MODEL_ID", _DEFAULT_MODEL)
        self.llm = LLM(
            model=model_id,
            # Deliberately no `quantization=`: vLLM reads the method off the
            # checkpoint's own config. That is the whole point of route B --
            # nothing here quantises anything, so there is no calibration set
            # and no second quantisation to conflate with the adapter's effect.
            #
            # enable_lora is False for a control run, not merely unused. Turning
            # it on replaces every q/k/v/o projection with a LoRA-capable
            # wrapper, so a control served with it on would not be the plain AWQ
            # serve that 170.05 was measured on.
            enable_lora=self.adapter is not None,
            max_loras=1,                    # one adapter per run, by design
            limit_mm_per_prompt={"image": 1},   # one crop per request, always
            max_model_len=_MAX_MODEL_LEN,
        )
        # One request object for the whole run. lora_int_id is what vLLM keys
        # its adapter cache on, and there is only ever one adapter here.
        self.lora_request = (
            LoRARequest(lora_name=self.adapter, lora_int_id=1,
                        lora_path=str(adapter_path))
            if self.adapter is not None else None)

    def _generate(self, pass_name: str, prompt: str, image: Image.Image,
                  max_tokens: int):
        """One constrained generation; returns (text, logprob steps).

        The adapter is chosen per REQUEST rather than suspended around a block:
        that is the whole of `VLMBackend._base_weights()` on this stack, and it
        cannot leak into the next pass the way a context manager can."""
        out = self.llm.chat(
            [{"role": "user", "content": [
                # chat_utils.MM_PARSER_MAP accepts a PIL image directly, so the
                # crop reaches the engine without a base64 round trip -- worth
                # having at ~900 crops per document.
                {"type": "image_pil", "image_pil": image},
                {"type": "text", "text": prompt},
            ]}],
            self.sampling_params_cls(**sampling_kwargs(max_tokens)),
            lora_request=(self.lora_request
                          if adapter_for_pass(pass_name, self.adapter)
                          else None),
        )[0].outputs[0]
        return out.text.strip(), out.logprobs

    def _read(self, pass_name: str, prompt: str, image: Image.Image,
              max_tokens: int) -> OcrResult:
        """A read pass: crop capped, confidence scored from the logprobs.

        The confidence is computed BEFORE the empty-text check, not after. An
        empty read scores 0.0 to match `VLMBackend._generate_text`, but a
        request sent without logprobs must still raise -- and the reads that
        come back empty are exactly the ones a silent 0.0 would look correct
        on."""
        text, steps = self._generate(pass_name, prompt,
                                     _cap_long_edge(image.convert("RGB")),
                                     max_tokens)
        conf = mean_confidence_from_logprobs(steps)
        return OcrResult(text=text, confidence=conf if text else 0.0)

    def read_region(self, image: Image.Image) -> OcrResult:
        return self._read("read", read_prompt(), image, self.max_new_tokens)

    def read_region_gdt(self, image: Image.Image) -> OcrResult:
        return self._read("gdt", _GDT_PROMPT, image, self.max_new_tokens)

    def read_notes_block(self, image: Image.Image) -> OcrResult:
        return self._read("notes", _NOTES_PROMPT, image, 512)

    def read_title_cell(self, image: Image.Image) -> OcrResult:
        return self._read("title", _TITLE_PROMPT, image, 128)

    def detect_regions(self, image: Image.Image):
        """Detection: base weights, full image, no crop cap.

        Uncapped because the boxes come back in pixels of the image sent and
        `extract()` maps them to the page by that scale -- downscaling here
        would shrink every box against a page that did not shrink. The
        transformers path does not cap it either."""
        from app.pipeline.detect import parse_detections
        text, _ = self._generate("detect", detect_prompt(),
                                 image.convert("RGB"), 1024)
        return parse_detections(text)
