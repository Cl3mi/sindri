"""Serving `read-lora-v1` on Qwen's official AWQ checkpoint, through vLLM.

Route B of `docs/plans/2026-09-08-deployment-routes.md`. PEFT cannot attach a
LoRA to an AWQ checkpoint (autoawq replaces every q/k/v/o projection with
WQLinear_GEMM), and merging into bf16 then re-quantising is route A. vLLM
applies adapters at runtime instead, and it works where PEFT refuses because
`lora/utils.py` dispatches on `LinearBase` -- "In vLLM, all linear layers
support LoRA" -- which is the class the AWQ path keeps, while `lora/layers.py`
reads a quantised base layer's device off its `qweight`.

Verified on the host against **vLLM 0.8.5.post1** (torch 2.6.0+cu124), which is
what this route runs. vLLM 0.28 was verified first and then discarded: it pulls
torch 2.13.0+cu130 and this host's driver is CUDA 12.4, so a real matmul dies
with "The NVIDIA driver on your system is too old (found version 12040)" --
while `torch.cuda.is_available()` still returns True, which is why only a real
op counts as proof here. On 0.8.5 `Qwen2_5_VLForConditionalGeneration` declares
SupportsMultiModal, SupportsLoRA and SupportsPP (0.28's SupportsQuant and
SupportsMRoPE do not exist on this version), and nothing in the tree guards
LoRA against quantisation.

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
    if not per_step:
        return 0.0
    # Guarded exactly as the transformers path is: review.LOW_CONF decides what
    # gets flagged on BOTH stacks, and one guarded and one not would make them
    # disagree about which rows are silent.
    mean = float(sum(per_step) / len(per_step))
    return mean if math.isfinite(mean) else 0.0


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


def chat_prompt(tokenizer, prompt: str) -> str:
    """One image+text user turn, rendered by the checkpoint's own chat template.

    `LLM.generate` takes a raw string, so route B has to apply the template
    `LLM.chat` would have applied -- and it must be the SAME string the
    transformers path sends, or the two backends prompt the same weights
    differently and the arm prices the difference rather than the adapter. Both
    renders were compared on the host for Qwen2.5-VL-72B-Instruct-AWQ and are
    byte-identical:

        <|im_start|>system\\nYou are a helpful assistant.<|im_end|>\\n
        <|im_start|>user\\n<|vision_start|><|image_pad|><|vision_end|>
        <prompt><|im_end|>\\n<|im_start|>assistant\\n

    Asked of the tokenizer rather than written out here, because a literal
    would keep rendering happily on the day the checkpoint's template changes
    and the only symptom would be worse reads. The image part carries no
    payload: it exists so the template emits the <|vision_start|> placeholder
    the crop's visual tokens are spliced into, and the crop itself travels
    beside the prompt in multi_modal_data.
    """
    return tokenizer.apply_chat_template(
        [{"role": "user", "content": [{"type": "image"},
                                      {"type": "text", "text": prompt}]}],
        add_generation_prompt=True, tokenize=False)


def engine_multiproc_method(env=None) -> str:
    """Start method for vLLM's EngineCore worker; defaults it to "spawn".

    Measured on the host, card 1, 2026-09-08. `get_backend()` probes
    `torch.cuda.is_available()` before it builds any backend, and on this image
    that CREATES the CUDA driver context in the parent -- torch takes the NVML
    path only when PYTORCH_NVML_BASED_CUDA_CHECK is set. V1's EngineCore then
    forks, and the child dies in `gpu_worker.init_device`:

        RuntimeError: Cannot re-initialize CUDA in forked subprocess. To use
        CUDA with multiprocessing, you must use the 'spawn' start method

    vLLM has an auto-override for exactly this and it does not fire, because it
    asks `torch.cuda.is_initialized()` -- torch's own flag, still False while
    the driver context already exists. So the default has to be set here.

    Spawn is chosen over un-poisoning the parent (PYTORCH_NVML_BASED_CUDA_CHECK,
    which lives in the shared selection path and would also change route A)
    because it holds no matter what touched CUDA first. The cost is that the
    child re-imports the main module, which is safe here: `app/eval/runner.py`
    guards on `if __name__ == "__main__"`, so the re-import runs imports and
    nothing else.

    This is worth a named function rather than a line in __init__ because the
    failure it prevents is SILENT: `_load_vlm_with_retry` retried the load three
    times, `get_backend()` swallowed the last error, and the run continued on
    Tesseract -- 20 garbled documents and exit code 0."""
    env = os.environ if env is None else env
    return env.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")


# Decode batch sizes worth capturing a CUDA graph for. `extract()` reads one
# crop at a time and _generate issues one request per call, so the batch is
# always 1; the rest is headroom that costs nothing, because a batch above
# max_capture_size runs eagerly rather than failing.
_CUDAGRAPH_CAPTURE_SIZES = [1, 2, 4, 8]


def compilation_config() -> dict:
    """vLLM compilation settings: capture graphs only for reachable batches.

    Measured on the host, card 1, 2026-09-08. Left at the default, the engine
    captures 67 shapes up to batch 512. The 72B AWQ engine reported
    "GPU KV cache size: 76,896 tokens" at 14:12:47 and was STILL capturing 49
    minutes later -- ~4900% CPU, compile cache static at 292 files, only graph
    memory growing -- and had to be killed before it ever served a request.
    run_gpu_queue.sh starts one container per stage, so that is paid per arm.

    Only the capture sizes are set. `level` is deliberately absent: V1 forces
    it to PIECEWISE in `VllmConfig.__post_init__` unless enforce_eager is set,
    so naming it here is at best ignored and at worst turns torch.compile off,
    which would make route B slower than the path it is being compared to for
    reasons that have nothing to do with the adapter."""
    return {"cudagraph_capture_sizes": list(_CUDAGRAPH_CAPTURE_SIZES)}


def engine_extra() -> dict:
    """Engine settings that are about getting a result at all.

    enforce_eager turns torch.compile OFF. Capping the cudagraph sizes fixed the
    49-minute capture, but the engine then died BEFORE serving a single request,
    inside Inductor's autotuner -- triton_heuristics.bench -> benchmark_gpu ->
    do_bench -> synchronize -> "CUDA error: an illegal memory access was
    encountered" -- on torch 2.6.0+cu124 / vLLM 0.8.5 / AWQ 72B / H100. V1
    forces compilation to PIECEWISE in VllmConfig.__post_init__ unless
    enforce_eager is set, so there is no smaller lever.

    It does not bias the experiment. r3-vllmlora is compared against
    r3-vllmcontrol, both served by this code with this setting, and review cost
    scores REVIEWER effort -- there is no inference-time term in it. Eager costs
    wall-clock, which belongs in the deployment write-up next to the NF4 route's
    2.3x, not in the delta."""
    return {"enforce_eager": True}


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
        # Before the import, not after: the EngineCore child is started from
        # the value of this variable, and forking it would fail on a parent
        # whose CUDA context the backend-selection probe already created.
        engine_multiproc_method()
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
            compilation_config=compilation_config(),
            **engine_extra(),
        )
        # The engine's own tokenizer, so chat_prompt renders with the template
        # that shipped with the checkpoint being served rather than one this
        # process guessed at.
        self.tokenizer = self.llm.get_tokenizer()
        # One request object for the whole run. lora_int_id is what vLLM keys
        # its adapter cache on, and there is only ever one adapter here.
        self.lora_request = (
            LoRARequest(lora_name=self.adapter, lora_int_id=1,
                        lora_path=str(adapter_path))
            if self.adapter is not None else None)

    def _generate(self, pass_name: str, prompt: str, image: Image.Image,
                  max_tokens: int):
        """One constrained generation; returns (text, logprob steps).

        `generate` rather than `chat`, because 0.8.5's
        chat_utils.MM_PARSER_MAP holds only {audio_url, image_embeds,
        image_url, input_audio, refusal, text, video_url}: there is no
        `image_pil` content part on this version, so chat() could take a crop
        only as a base64 data URI and would re-encode ~900 crops per document
        for nothing. generate() accepts the PIL object itself under
        multi_modal_data, and chat_prompt supplies the template chat() would
        otherwise have applied.

        use_tqdm=False because this issues one request per callout and the
        stage log run_gpu_queue.sh tees to disk is the only surviving record of
        a run's timings -- `podman run --rm` destroys the container's own.

        The adapter is chosen per REQUEST rather than suspended around a block:
        that is the whole of `VLMBackend._base_weights()` on this stack, and it
        cannot leak into the next pass the way a context manager can."""
        out = self.llm.generate(
            {"prompt": chat_prompt(self.tokenizer, prompt),
             "multi_modal_data": {"image": image}},
            self.sampling_params_cls(**sampling_kwargs(max_tokens)),
            lora_request=(self.lora_request
                          if adapter_for_pass(pass_name, self.adapter)
                          else None),
            use_tqdm=False,
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
