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


# --- how each model is placed on its card -----------------------------------

def test_no_device_keeps_the_load_path_every_measurement_was_taken_with():
    """`device_map="auto"` is what produced the frozen baseline, r3-awqcontrol
    and every other committed number. The hybrid must not change it for runs
    that are not hybrids."""
    from app.pipeline.ocr.vlm_backend import device_map_for
    assert device_map_for(None) == "auto"


def test_a_named_device_places_the_whole_model_on_that_card():
    """Two models cannot both use "auto": the first would spread across both
    visible cards and leave no room for the second. An explicit single-device
    map is the only placement that holds, and with ONE visible card "auto"
    resolves to exactly this, which is why it is measurement-neutral."""
    from app.pipeline.ocr.vlm_backend import device_map_for
    assert device_map_for("cuda:1") == {"": "cuda:1"}


# --- the backend itself -----------------------------------------------------
#
# Doubles rather than checkpoints: __init__ otherwise loads ~62 GB of weights.
# Same reasoning as tests/test_vlm_adapter_scope.py, which duck-types PEFT.

from app.pipeline.ocr.base import OcrResult


class _Recorder:
    """One model. Records every pass it was asked to serve, in order."""

    def __init__(self, name):
        self.name = name
        self.calls = []

    def detect_regions(self, image):
        self.calls.append("detect")
        return []

    def read_region(self, image):
        self.calls.append("read")
        return OcrResult(text="1,2", confidence=0.9)

    def read_region_gdt(self, image):
        self.calls.append("gdt")
        return OcrResult(text="", confidence=0.0)

    def read_notes_block(self, image):
        self.calls.append("notes")
        return OcrResult(text="[]", confidence=0.9)

    def read_title_cell(self, image):
        self.calls.append("title")
        return OcrResult(text="{}", confidence=0.9)


def _hybrid(monkeypatch, env=None):
    """A HybridBackend over two recorders, with the builds recorded too."""
    built = []
    detect, read = _Recorder("detect"), _Recorder("read")

    def build(model_id, device):
        built.append((model_id, device))
        return detect if len(built) == 1 else read

    monkeypatch.setattr(hb, "_build_vlm", build)
    monkeypatch.setenv("VLM_MODEL_ID", "Qwen/Qwen2.5-VL-72B-Instruct-AWQ")
    monkeypatch.setenv("VLM_DETECT_MODEL_ID", "Qwen/Qwen2.5-VL-32B-Instruct-AWQ")
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    return hb.HybridBackend(), detect, read, built


def test_each_checkpoint_is_built_from_its_own_id_on_its_own_card(monkeypatch):
    """The detect model is built FIRST and on the first card. Order matters on a
    contended host: whichever loads first takes what it needs, and the 32B is
    the one this arm exists for -- a partial load must fail on the 72B, whose
    absence is obvious, not on the 32B, whose absence would look like a working
    run of 569 predictions."""
    _, _, _, built = _hybrid(monkeypatch)
    assert built == [("Qwen/Qwen2.5-VL-32B-Instruct-AWQ", "cuda:0"),
                     ("Qwen/Qwen2.5-VL-72B-Instruct-AWQ", "cuda:1")]


def test_detection_runs_on_the_detect_model_only(monkeypatch):
    """The arm in one assertion: n_pred must be the 32B's 890, not the 72B's
    569, and that is the registered void gate."""
    backend, detect, read, _ = _hybrid(monkeypatch)
    backend.detect_regions(_image())
    assert detect.calls == ["detect"]
    assert read.calls == []


def test_every_transcription_runs_on_the_read_model_only(monkeypatch):
    """The other half. The 32B reads at field_acc 0.2614 against the 72B's
    0.4798, so a read that leaked to the detect model would report the worst
    reader measured as the hybrid's -- and it would read as "the 32B's boxes
    are unreadable", which is a conclusion, not a bug report."""
    backend, detect, read, _ = _hybrid(monkeypatch)
    backend.read_region(_image())
    backend.read_region_gdt(_image())
    backend.read_notes_block(_image())
    backend.read_title_cell(_image())
    assert read.calls == ["read", "gdt", "notes", "title"]
    assert detect.calls == []


def test_the_cards_can_be_swapped_at_launch(monkeypatch):
    """Occupancy varies between the two H100s and the loser of that race falls
    back to Tesseract, which garbles every document while looking like it
    worked."""
    _, _, _, built = _hybrid(monkeypatch, {"VLM_DETECT_DEVICE": "cuda:1",
                                           "VLM_READ_DEVICE": "cuda:0"})
    assert [d for _, d in built] == ["cuda:1", "cuda:0"]


def _image():
    from PIL import Image
    return Image.new("RGB", (64, 64), "white")


def test_a_whole_document_routes_every_localisation_to_the_detect_model(
        sample_pdf, tmp_path, monkeypatch):
    """The acceptance test, and the one that catches a MISSING delegation.

    extract() calls detect_regions from three places -- the characteristic
    detector, the notes locator and loose_text -- and reads from four. A method
    the hybrid forgot to delegate would fall through to whichever model happens
    to hold it, and nothing in a report would say so. Running the real pipeline
    is the only check that covers all seven call sites at once."""
    from app.pipeline.extract import extract

    backend, detect, read, _ = _hybrid(monkeypatch)
    extract(sample_pdf, work_dir=tmp_path, dpi=300, backend=backend)

    assert detect.calls, "the detect model served nothing at all"
    assert set(detect.calls) == {"detect"}, (
        f"a transcription reached the detect model: {set(detect.calls)}")
    assert "detect" not in read.calls, (
        f"a localisation reached the read model: {set(read.calls)}")
