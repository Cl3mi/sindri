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


# --- the backend itself: request shape, scoping, budgets, crops -------------
#
# The doubles below are duck-typed rather than built on vllm, for the reason
# tests/test_vlm_adapter_scope.py gives for peft and torch: vllm is not
# installed outside the GPU image, and `VLLMBackend.__init__` loads ~40 GB of
# weights. Backends are therefore built with `object.__new__` and the four
# attributes `__init__` would have set -- the same idiom that file uses.
#
# Every call shape asserted here was verified against vLLM 0.28.0 on the host
# (`inspect.signature` and the installed source), not against documentation:
#   LLM.chat(messages, sampling_params, *, lora_request=...) -> [RequestOutput]
#   RequestOutput.outputs[0].text / .logprobs   (list[dict[int, Logprob]])
#   {"type": "image_pil", "image_pil": <PIL.Image>} is in chat_utils.MM_PARSER_MAP
#   LoRARequest(lora_name, lora_int_id, lora_path)

from PIL import Image

from app.pipeline.ocr import vlm_backend
from app.pipeline.ocr import vllm_backend as vb

# Stands in for a built vllm.lora.request.LoRARequest. Identity is all the
# backend needs: it holds ONE adapter for the whole run and either attaches it
# to a request or does not.
_LORA = "LoRARequest(read-lora-v1)"


class _Completion:
    def __init__(self, text, logprobs):
        self.text = text
        self.logprobs = logprobs


class _RequestOutput:
    def __init__(self, text, logprobs):
        self.outputs = [_Completion(text, logprobs)]


class _FakeLLM:
    """Duck of `vllm.LLM`, recording every request so the assertions can read
    the prompt, the image, the token budget and the adapter off it."""

    def __init__(self, text="42 +0,1 -0,1", logprobs=None):
        self.text = text
        self.logprobs = ([_step(math.log(0.9))] if logprobs is None
                         else logprobs)
        self.calls = []

    def chat(self, messages, sampling_params, lora_request=None):
        self.calls.append({"messages": messages,
                           "sampling_params": sampling_params,
                           "lora_request": lora_request})
        return [_RequestOutput(self.text, self.logprobs)]

    # --- readers over the last call, so the tests stay about the behaviour ---
    @property
    def last(self):
        return self.calls[-1]

    @property
    def sent_prompt(self):
        return self.last["messages"][0]["content"][1]["text"]

    @property
    def sent_image(self):
        return self.last["messages"][0]["content"][0]["image_pil"]

    @property
    def sent_budget(self):
        return self.last["sampling_params"]["max_tokens"]


def _backend(llm, adapter="read-lora-v1"):
    """A VLLMBackend around a double. `__init__` builds an LLM engine over a
    72B checkpoint, so the attributes it sets are constructed here directly --
    exactly as tests/test_vlm_adapter_scope.py does for the transformers path.

    `sampling_params_cls=dict` is not a shortcut: SamplingParams is constructed
    as `SamplingParams(**sampling_kwargs(n))`, so `dict` is a faithful stand-in
    that also lets a test read the budget back."""
    b = object.__new__(vb.VLLMBackend)
    b.llm = llm
    b.sampling_params_cls = dict
    b.adapter = adapter
    b.lora_request = _LORA if adapter else None
    b.max_new_tokens = 40
    return b


def _crop(size=(64, 64)):
    return Image.new("RGB", size, "white")


# --- prompts: the comparability pin -----------------------------------------

def test_every_pass_sends_the_prompt_the_transformers_path_sends():
    """`app.eval.runner._prompt_sha256` hashes vlm_backend's five effective
    prompts and every committed measurement carries aa7659f1929184ea. A vLLM
    run that reworded even one of them would report the same hash while reading
    something else, so route B must send these strings and not copies of
    them."""
    llm = _FakeLLM()
    b = _backend(llm)

    b.read_region(_crop())
    assert llm.sent_prompt == vlm_backend.read_prompt()
    b.read_region_gdt(_crop())
    assert llm.sent_prompt == vlm_backend._GDT_PROMPT
    b.read_notes_block(_crop())
    assert llm.sent_prompt == vlm_backend._NOTES_PROMPT
    b.read_title_cell(_crop())
    assert llm.sent_prompt == vlm_backend._TITLE_PROMPT
    b.detect_regions(_crop())
    assert llm.sent_prompt == vlm_backend.detect_prompt()


def test_the_read_prompt_follows_the_variant_in_effect(monkeypatch):
    """Prompt variants are selected per run by environment variable, and the
    variant name reaches RunConfig.extra. Reading the module constant instead
    of read_prompt() would silently run the base arm under a variant arm's run
    name -- the failure `_select` refuses to allow."""
    monkeypatch.setenv("SINDRI_READ_PROMPT", "center")
    llm = _FakeLLM()
    _backend(llm).read_region(_crop())
    assert llm.sent_prompt == vlm_backend._PROMPT_CENTER


# --- the read/detect scoping, now as a per-request adapter ------------------

def test_detection_carries_no_adapter():
    """Rung 3's first arm served the read adapter over detection too:
    false_detection 607 -> 931, +10.00 review cost, and the read stage's
    contribution was not recoverable from the total. vLLM takes the adapter per
    request, so this is the whole of `VLMBackend._base_weights()` here."""
    llm = _FakeLLM()
    _backend(llm).detect_regions(_crop())
    assert llm.last["lora_request"] is None


def test_every_read_pass_carries_the_adapter():
    """The other half of the scoping: disabling it everywhere would serve the
    base model under a treatment arm's run name."""
    llm = _FakeLLM()
    b = _backend(llm)
    b.read_region(_crop())
    b.read_region_gdt(_crop())
    b.read_notes_block(_crop())
    b.read_title_cell(_crop())
    assert [c["lora_request"] for c in llm.calls] == [_LORA] * 4


def test_the_adapter_does_not_leak_across_passes():
    """`extract()` interleaves the passes per document -- detect the page, then
    read every region it found. On the transformers path a leaked
    `disable_adapter` would turn the rest of a document into a base run; here
    the equivalent failure is caching the request object across calls."""
    llm = _FakeLLM()
    b = _backend(llm)
    b.detect_regions(_crop())
    b.read_region(_crop())
    b.detect_regions(_crop())
    assert [c["lora_request"] for c in llm.calls] == [None, _LORA, None]


def test_a_base_run_attaches_no_adapter_anywhere():
    """The control arm. With no adapter selected every request must go to the
    unmodified AWQ checkpoint, which is what 170.05 was measured on."""
    llm = _FakeLLM()
    b = _backend(llm, adapter=None)
    b.read_region(_crop())
    b.detect_regions(_crop())
    assert [c["lora_request"] for c in llm.calls] == [None, None]


# --- token budgets: a wrong one truncates silently --------------------------

def test_each_pass_gets_its_own_token_budget():
    """40 for a callout, 40 for a GD&T frame, 512 for the notes block, 128 for
    a title cell, 1024 for detection -- the budgets the transformers path uses.
    One shared budget would truncate the notes block, and a truncated JSON
    array parses to FEWER notes rather than to an error, so the loss is
    invisible in the output."""
    llm = _FakeLLM(text="[]")
    b = _backend(llm)
    b.read_region(_crop())
    b.read_region_gdt(_crop())
    b.read_notes_block(_crop())
    b.read_title_cell(_crop())
    b.detect_regions(_crop())
    assert [c["sampling_params"]["max_tokens"] for c in llm.calls] == \
        [40, 40, 512, 128, 1024]


def test_the_request_is_greedy_and_asks_for_logprobs():
    """Determinism is the comparison method (CLAUDE.md section 5), and the
    confidence a row is flagged on comes from the logprobs. Both must survive
    the trip from sampling_kwargs into the actual request."""
    llm = _FakeLLM()
    _backend(llm).read_region(_crop())
    assert llm.last["sampling_params"]["temperature"] == 0.0
    assert llm.last["sampling_params"]["logprobs"] >= 1


# --- crops: the vision encoder OOMs on a full-size legend -------------------

def test_a_large_read_crop_is_downscaled():
    """A full legend crop (~2890x1436 at 300 dpi) OOMs the vision encoder at
    native size: the allocator aborts mid-generate, the read wrapper swallows
    it to "", and the whole notes/marks table comes back empty. The
    transformers path caps the long edge at 1600 and this must too, or the two
    backends read different pixels from the same crop."""
    llm = _FakeLLM()
    _backend(llm).read_notes_block(_crop((2890, 1436)))
    sent = llm.sent_image
    assert max(sent.size) <= vlm_backend._MAX_READ_LONG_EDGE
    assert sent.size != (2890, 1436)


def test_detection_sees_the_full_image():
    """Detection boxes are in pixels of the image it was given, and
    `extract()` maps them back to the page by that scale. Capping the detect
    tile would shrink every box against a page coordinate system that did not
    shrink -- and the transformers path does not cap it either."""
    llm = _FakeLLM(text="[]")
    _backend(llm).detect_regions(_crop((2890, 1436)))
    assert llm.sent_image.size == (2890, 1436)


def test_the_image_travels_as_a_pil_content_part():
    """vLLM 0.28's chat_utils.MM_PARSER_MAP accepts {"type": "image_pil",
    "image_pil": <PIL.Image>}, so the crop goes straight to the engine. The
    alternative -- a base64 data URI -- would re-encode every one of the ~900
    crops per document for nothing."""
    llm = _FakeLLM()
    _backend(llm).read_region(_crop())
    part = llm.last["messages"][0]["content"][0]
    assert part["type"] == "image_pil"
    assert isinstance(part["image_pil"], Image.Image)


# --- results -----------------------------------------------------------------

def test_confidence_comes_from_the_returned_logprobs():
    """review.LOW_CONF = 0.8 decides what gets flagged and is worth 3.00 review
    cost. A backend that returned a constant, or the cumulative_logprob, would
    move rows across that line and the arm would measure the flagging change."""
    llm = _FakeLLM(text="Ø7", logprobs=[_step(math.log(0.9)),
                                        _step(math.log(0.7))])
    res = _backend(llm).read_region(_crop())
    assert res.text == "Ø7"
    assert res.confidence == pytest.approx(0.8)


def test_an_empty_read_scores_zero_confidence():
    """The transformers path scores an empty read 0.0 rather than averaging the
    confidence of the tokens it did not emit. Matching it matters because
    `escaped_rate` counts rows that were wrong and NOT flagged."""
    llm = _FakeLLM(text="", logprobs=[_step(math.log(0.99))])
    assert _backend(llm).read_region(_crop()).confidence == 0.0


def test_missing_logprobs_raise_even_when_the_read_is_empty():
    """Zeroing an empty read must not become a way past the None guard: a
    request sent without logprobs returns None for EVERY row, and the reads
    that come back empty are exactly the ones a silent 0.0 would look correct
    on."""
    llm = _FakeLLM(text="", logprobs=None)
    llm.logprobs = None
    with pytest.raises(ValueError, match="logprobs"):
        _backend(llm).read_region(_crop())


def test_whitespace_around_a_read_is_stripped():
    """The parser matches on the transcription itself; the transformers path
    strips, so a leading newline here would be a difference between the two
    backends that shows up as a read failure."""
    llm = _FakeLLM(text="  42 +0,1 -0,1\n")
    assert _backend(llm).read_region(_crop()).text == "42 +0,1 -0,1"


def test_detections_come_back_parsed():
    """detect_regions returns Detection objects, not text: `parse_detections`
    is what validates kinds against detect._KINDS and drops malformed boxes,
    and the pipeline downstream indexes .box and .kind."""
    llm = _FakeLLM(text='[{"box":[1,2,30,40],"kind":"gdt"}]')
    dets = _backend(llm).detect_regions(_crop())
    assert len(dets) == 1
    assert dets[0].box == (1, 2, 30, 40)
    assert dets[0].kind == "gdt"
