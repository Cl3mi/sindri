"""The crop the reader is handed, as a knob that a run can record.

`r3-hybrid` measured that the crop dominates read accuracy: hold the reader and
change the boxes and `field_acc` moves -0.206, while holding the boxes and
changing the reader moves +0.013 and the best fine-tune on fixed boxes moved
+0.039. Crop preparation is the only untested family on that lever -- every box
lever tried so far was about WHICH boxes exist (tile size, the merge knobs,
render resolution, both prompts), never about what crop a box becomes.

These constants were module-level, so an arm changing them would have been
invisible in every report AND would have hit the resume trap: `_reusable_dump`
compares the whole `RunConfig`, so a knob it cannot see makes a re-run skip
every document as "already predicted" across the change being measured. That
trap is recorded in CLAUDE.md §5 because it was already paid for once.
"""
import pytest
from PIL import Image

from app.pipeline import extract as ex


def _img():
    return Image.new("RGB", (200, 200), "white")


# --- defaults: every historical dump must keep the config it has -------------

def test_the_defaults_are_exactly_what_every_measurement_was_taken_with():
    """6 px of context, a 40 px floor, at most 3x upscaling. The frozen
    baseline, r3-awqcontrol, the LoRA's training crops and every arm in the
    campaign were produced with these."""
    assert ex.resolve_crop_knobs({}) == (6, 40, 3.0)


def test_an_unchanged_run_records_nothing():
    """Emitting these keys unconditionally would change the RunConfig of every
    dump ever taken and force a re-predict of the whole corpus for no
    measurement -- the same discipline that keeps `quant`, `adapter`,
    `serving_backend` and `detect_model` absent when they do not apply."""
    assert ex.active_crop_knobs({}) == {}


# --- a changed run records ALL of them --------------------------------------

def test_changing_one_knob_records_all_three():
    """Self-describing on purpose. Recording only the changed key would make a
    dump's meaning depend on what the defaults were on the day it ran, and the
    defaults are exactly the thing an arm moves."""
    env = {"SINDRI_CROP_PAD": "24"}
    assert ex.resolve_crop_knobs(env) == (24, 40, 3.0)
    assert ex.active_crop_knobs(env) == {"crop_pad": 24, "crop_min_h": 40,
                                         "crop_max_upscale": 3.0}


def test_every_knob_is_settable():
    env = {"SINDRI_CROP_PAD": "0", "SINDRI_CROP_MIN_H": "64",
           "SINDRI_CROP_MAX_UPSCALE": "2.0"}
    assert ex.resolve_crop_knobs(env) == (0, 64, 2.0)


# --- refusing, rather than falling back -------------------------------------

@pytest.mark.parametrize("env", [
    {"SINDRI_CROP_PAD": "wide"},
    {"SINDRI_CROP_PAD": "-4"},
    {"SINDRI_CROP_MIN_H": "-1"},
    {"SINDRI_CROP_MAX_UPSCALE": "0.5"},
    {"SINDRI_CROP_MAX_UPSCALE": "lots"},
])
def test_a_bad_value_loses_the_arm_rather_than_the_measurement(env):
    """Same rule as an unknown prompt variant or an unknown adapter: falling
    back to the default would serve the control configuration under a treatment
    arm's run name, and the result would read as "the crop made no difference"
    -- the single most misleading outcome available. A downscaling max_upscale
    (<1) is refused too: it inverts the knob's meaning while still looking like
    a number someone chose."""
    with pytest.raises(ValueError):
        ex.resolve_crop_knobs(env)


# --- the knobs must actually reach the crop ---------------------------------

def test_the_pad_knob_changes_the_crop_the_reader_gets(monkeypatch):
    """A resolver nothing reads is worse than no resolver: it would record a
    knob in RunConfig that the pipeline ignored, and the arm would report a
    treatment it never applied."""
    base = ex._prep_crop(_img(), (40, 40, 140, 140), 200, 200, pad=ex._CROP_PAD)
    monkeypatch.setenv("SINDRI_CROP_PAD", "24")
    wide = ex._prep_crop(_img(), (40, 40, 140, 140), 200, 200,
                         pad=ex.resolve_crop_knobs()[0])
    assert base.size == (112, 112)          # 100 + 2*6
    assert wide.size == (148, 148)          # 100 + 2*24


def test_the_upscale_knobs_reach_prep_crop(monkeypatch):
    """_prep_crop reads the floor and the ceiling from the environment at call
    time, so an arm does not have to thread them through every call site."""
    monkeypatch.setenv("SINDRI_CROP_MIN_H", "80")
    crop = ex._prep_crop(_img(), (40, 40, 140, 80), 200, 200, pad=0)  # 40 px tall
    assert crop.height == 80         # 2x, inside the 3x ceiling


def test_the_upscale_ceiling_still_binds(monkeypatch):
    """The floor cannot be reached by more than the ceiling allows -- a 10 px
    crop at min_h 80 would need 8x, and blowing a callout up that far produces
    an image the reader has never seen."""
    monkeypatch.setenv("SINDRI_CROP_MIN_H", "80")
    monkeypatch.setenv("SINDRI_CROP_MAX_UPSCALE", "2.0")
    crop = ex._prep_crop(_img(), (40, 40, 50, 50), 200, 200, pad=0)  # 10 px tall
    assert crop.height == 20


# --- training and inference cannot diverge ----------------------------------

def test_training_crops_follow_the_same_knob():
    """app/train/dataset.py imports the pipeline's own crop functions because
    "a training crop that differs from an inference crop teaches the model the
    wrong input distribution, which would show up as a LoRA that helps on paper
    and not in the pipeline". A knob that moved inference but not training would
    reintroduce exactly that, silently, the first time anyone changed it."""
    import app.train.dataset as ds
    src = __import__("inspect").getsource(ds)
    assert "_CROP_PAD" not in src, (
        "the training crop must resolve the knob, not pin the default constant")
    assert "resolve_crop_knobs" in src


# --- and the dump has to say so ---------------------------------------------

def test_the_knobs_reach_the_dump(monkeypatch):
    """Without this the arm and its control are indistinguishable in every
    recorded field, and _reusable_dump skips all 20 documents as "already
    predicted" across the change being measured."""
    from app.eval.runner import _predict_extra
    monkeypatch.setenv("SINDRI_CROP_PAD", "24")
    extra = _predict_extra(detect_only=False)
    assert extra["crop_pad"] == 24
    assert extra["crop_min_h"] == 40


def test_an_unchanged_run_still_has_no_crop_keys(monkeypatch):
    for k in ("SINDRI_CROP_PAD", "SINDRI_CROP_MIN_H", "SINDRI_CROP_MAX_UPSCALE"):
        monkeypatch.delenv(k, raising=False)
    from app.eval.runner import _predict_extra
    assert "crop_pad" not in _predict_extra(detect_only=False)
