"""Two sets of weights in one pipeline: the 32B localises, the 72B transcribes.

Handoff 2026-09-09 §3. Under the scope policy the 32B is the best DETECTOR and
the worst READER measured -- missed 70 against the 72B's 88 at recall 0.7749
against 0.7170, field_acc 0.2614 against 0.4798 -- and Rung 1 established that
the detector's WEIGHTS are the only thing that has ever moved `missed`: render
resolution, tile size and both merge knobs all lost.

The split this module makes is not new machinery. `VLMBackend._base_weights()`
already separates detection from reading so a read-trained adapter cannot reach
detection, and `vllm_backend.adapter_for_pass` expresses the same split
per-request. This does it with two checkpoints instead of one checkpoint and an
adapter, which is the only form that can carry a DIFFERENT model into detection.

The registered prediction, the void gates and the cost arithmetic are in
docs/plans/2026-09-09-hybrid-arm-prediction.md, written before this file.
"""
import os

# Which set of weights serves each pass. An explicit map with no default, for
# the reason `vllm_backend._ADAPTED_PASSES` gives: a pass added later that
# silently inherited either answer is invisible in the output. Routing a read to
# the detect model reports the 32B's reading as the hybrid's; routing a
# detection to the read model silently un-does the arm and it comes back as
# "the 32B's boxes changed nothing".
#
# "detect" covers every LOCALISATION, not only detect_characteristics: the notes
# locator (notes_block.locate_notes_block) and loose_text (title_block) also call
# detect_regions, and they run on the 32B here exactly as they did in the
# `r3-32bawq` run this arm's n_pred gate is measured against. Both are pure
# detection -- neither reads anything to choose its region -- so the masking that
# precedes detection is identical to that run's and n_pred stays comparable.
_PASS_MODELS = {"detect": "detect",
                "read": "read", "gdt": "read", "notes": "read",
                "title": "read"}

# One model per H100. 32B AWQ needs ~44 GB and 72B AWQ ~43 GB (handoff §4, where
# the peak is set by vision activations on large crops rather than by weights),
# so 87 GB does not fit on one 80 GB card.
_DEFAULT_DETECT_DEVICE = "cuda:0"
_DEFAULT_READ_DEVICE = "cuda:1"


def model_for_pass(pass_name: str) -> str:
    """"detect" or "read": which of the two checkpoints serves this pass."""
    if pass_name not in _PASS_MODELS:
        raise ValueError(
            f"unclassified pass {pass_name!r}: it must be declared in "
            f"_PASS_MODELS as localisation or transcription. Guessing would "
            f"either report the detect model's reading as the hybrid's or "
            f"silently un-do the arm, and neither is visible in the output. "
            f"Known: {sorted(_PASS_MODELS)}.")
    return _PASS_MODELS[pass_name]


def _env(env):
    return os.environ if env is None else env


def active_detect_model(env=None) -> str:
    """The checkpoint serving detection. VLM_MODEL_ID keeps naming the READ
    model, so every existing stage, image and dump keeps its meaning and
    RunConfig.model_id still names the weights the VALUES came from.

    Raises when unset rather than falling back to the read model. That fallback
    would serve the 72B on both passes under the hybrid's run name: n_pred would
    come back 569 instead of 890 and the arm would read as "the 32B's boxes
    changed nothing", which is a complete, plausible, wrong result -- the exact
    failure `resolve_adapter` refuses for adapters."""
    name = _env(env).get("VLM_DETECT_MODEL_ID") or None
    if name is None:
        raise ValueError(
            "OCR_BACKEND=hybrid needs VLM_DETECT_MODEL_ID (the localisation "
            "checkpoint, e.g. Qwen/Qwen2.5-VL-32B-Instruct-AWQ). Refusing to "
            "fall back to VLM_MODEL_ID: that serves one model on both passes "
            "and reports it as the hybrid.")
    return name


def active_hybrid_config(env=None) -> dict:
    """The detect model, for RunConfig.extra -- empty when there is no hybrid.

    The container's git_sha is always "unknown" and _reusable_dump compares the
    whole RunConfig, so without this key a hybrid dump and a plain 72B dump
    differ in nothing a resume can see. Absent for every historical dump on
    purpose: emitting it for them would change their RunConfig and break dump
    reuse across the whole corpus for no measurement."""
    name = _env(env).get("VLM_DETECT_MODEL_ID") or None
    return {"detect_model": name} if name else {}


def hybrid_devices(env=None):
    """(detect_device, read_device). Different cards, always.

    Which card holds which model is a launch decision because occupancy varies
    between the two H100s on this host, and a 72B load into an occupied card
    falls back to Tesseract and garbles every document while looking like it
    worked.

    Two models on one card is refused rather than attempted: 87 GB on an 80 GB
    card OOMs partway through the first document, and `extract._safe_read`
    swallows read exceptions -- so it would not crash, it would produce a full
    run of empty values that scores as a catastrophically bad reader."""
    e = _env(env)
    detect = e.get("VLM_DETECT_DEVICE") or _DEFAULT_DETECT_DEVICE
    read = e.get("VLM_READ_DEVICE") or _DEFAULT_READ_DEVICE
    if detect == read:
        raise ValueError(
            f"VLM_DETECT_DEVICE and VLM_READ_DEVICE are both {detect!r}: the "
            f"32B (~44 GB) and the 72B (~43 GB) do not fit on one 80 GB H100. "
            f"An OOM here does not crash the run -- _safe_read swallows it -- "
            f"so it would come back as a full run of empty reads.")
    return detect, read
