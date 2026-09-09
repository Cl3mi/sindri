"""The hybrid arm: 32B weights localise, 72B weights transcribe.

Handoff 2026-09-09 §3. The 32B is the best detector and the worst reader
measured -- missed 70 against the 72B's 88, field_acc 0.2614 against 0.4798 --
and nothing else in the campaign has ever moved `missed`. The prediction and the
void gates are registered in docs/plans/2026-09-09-hybrid-arm-prediction.md.

These tests are pure: nothing here imports torch or loads a checkpoint. The
routing table and the configuration are the part that can be wrong silently, and
a wrong route produces a complete, plausible run under the arm's name -- which
is the failure mode `resolve_adapter` and `adapter_for_pass` already refuse.
"""
import pytest

from app.pipeline.ocr import hybrid_backend as hb


# --- which weights serve which pass -----------------------------------------

def test_detection_is_the_only_pass_served_by_the_detect_model():
    """The whole arm in one assertion. Detection is what the 32B is here for;
    every transcription must stay on the 72B, or the arm measures the 32B's
    reading -- which is already known and is the worst measured."""
    assert hb.model_for_pass("detect") == "detect"
    for read_pass in ("read", "gdt", "notes", "title"):
        assert hb.model_for_pass(read_pass) == "read", read_pass


def test_an_unclassified_pass_raises_rather_than_defaulting():
    """Same rule as vllm_backend.adapter_for_pass: a pass added later must be
    declared, because either default is invisible in the output. Routing a new
    read to the 32B would report the 32B's reading as the hybrid's; routing a
    new detection to the 72B would silently un-do the arm."""
    with pytest.raises(ValueError, match="unclassified pass"):
        hb.model_for_pass("loose_text")


# --- configuration ----------------------------------------------------------

def test_the_detect_model_comes_from_its_own_variable():
    """VLM_MODEL_ID keeps naming the READ model, so every existing stage, image
    and dump keeps its meaning and RunConfig.model_id still names what produced
    the values."""
    env = {"VLM_MODEL_ID": "Qwen/Qwen2.5-VL-72B-Instruct-AWQ",
           "VLM_DETECT_MODEL_ID": "Qwen/Qwen2.5-VL-32B-Instruct-AWQ"}
    assert hb.active_detect_model(env) == "Qwen/Qwen2.5-VL-32B-Instruct-AWQ"


def test_a_missing_detect_model_refuses_to_fall_back_to_the_read_model():
    """Falling back would serve the 72B on both passes and report it as the
    hybrid: n_pred would come back 569 instead of 890 and the arm would read as
    "the 32B's boxes changed nothing" -- the single most misleading outcome
    available here, and the one resolve_adapter was written to prevent."""
    with pytest.raises(ValueError, match="VLM_DETECT_MODEL_ID"):
        hb.active_detect_model({"VLM_MODEL_ID": "Qwen/Qwen2.5-VL-72B-Instruct-AWQ"})


def test_the_config_records_the_detect_model_for_the_dump():
    """git_sha is always "unknown" in the container and _reusable_dump compares
    the whole RunConfig, so without this a hybrid dump and a plain 72B dump
    differ in nothing a resume can see -- and a re-run would skip all 15
    documents as "already predicted" across the change being measured."""
    env = {"VLM_DETECT_MODEL_ID": "Qwen/Qwen2.5-VL-32B-Instruct-AWQ"}
    assert hb.active_hybrid_config(env) == {
        "detect_model": "Qwen/Qwen2.5-VL-32B-Instruct-AWQ"}


def test_no_hybrid_config_when_no_detect_model_is_selected():
    """Every historical dump was predicted without one. Emitting a key for them
    would change their RunConfig and break dump reuse across the whole corpus
    for no measurement -- the reasoning _serving_backend() already follows."""
    assert hb.active_hybrid_config({}) == {}


# --- device placement -------------------------------------------------------

def test_the_two_models_land_on_different_cards_by_default():
    """32B AWQ needs ~44 GB and 72B AWQ ~43 GB (handoff §4): 87 GB does not fit
    on one 80 GB H100. One model per card is the only placement that works, and
    it is why the hybrid stage is the one exception to run_gpu_queue.sh's
    one-card rule."""
    assert hb.hybrid_devices({}) == ("cuda:0", "cuda:1")


def test_the_cards_can_be_swapped_without_a_code_change():
    """Occupancy on this host varies between the two H100s and a 72B load into
    an occupied card falls back to Tesseract, which then garbles every document
    while looking like it worked. Which card holds which model has to be a
    launch decision."""
    env = {"VLM_DETECT_DEVICE": "cuda:1", "VLM_READ_DEVICE": "cuda:0"}
    assert hb.hybrid_devices(env) == ("cuda:1", "cuda:0")


def test_both_models_on_one_card_is_refused():
    """87 GB of weights on an 80 GB card OOMs partway through the first
    document, and _safe_read swallows read exceptions -- so it would not crash,
    it would return a full run of empty values. Refuse at construction instead."""
    with pytest.raises(ValueError, match="do not fit on one"):
        hb.hybrid_devices({"VLM_DETECT_DEVICE": "cuda:0",
                           "VLM_READ_DEVICE": "cuda:0"})
