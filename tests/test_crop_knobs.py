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

def test_the_frozen_baseline_is_what_every_pre_2026_09_16_dump_implies():
    """6 px of context, a 40 px floor, at most 3x upscaling. The frozen Rung-0
    baseline, r3-awqcontrol, the LoRA's training crops and every arm up to
    2026-09-16 were produced with these, and the recording is relative to them
    for as long as those dumps exist."""
    assert ex._BASELINE_CROP_KNOBS == (6, 40, 3.0)


def test_a_run_at_the_baseline_records_nothing():
    """Emitting these keys for a baseline run would change the RunConfig of
    every dump ever taken and force a re-predict of the whole corpus for no
    measurement -- the same discipline that keeps `quant`, `adapter`,
    `serving_backend` and `detect_model` absent when they do not apply."""
    assert ex.active_crop_knobs({"SINDRI_CROP_PAD": "6"}) == {}


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
    # An 80x80 box with room for the largest pad on every side: _prep_crop
    # clamps at the page edge, so a box near the border would measure the clamp
    # rather than the knob.
    box = (60, 60, 140, 140)
    base = ex._prep_crop(_img(), box, 200, 200, pad=ex._BASELINE_CROP_KNOBS[0])
    monkeypatch.setenv("SINDRI_CROP_PAD", "48")
    wide = ex._prep_crop(_img(), box, 200, 200, pad=ex.resolve_crop_knobs()[0])
    assert base.size == (92, 92)            # 80 + 2*6
    assert wide.size == (176, 176)          # 80 + 2*48


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


def test_a_baseline_pinned_run_has_no_crop_keys_in_the_dump(monkeypatch):
    """The dump of a run pinned back to 6/40/3.0 must be indistinguishable from
    the dumps taken before the knob existed -- that is what lets an old arm be
    re-run and its documents legitimately reused."""
    for k in ("SINDRI_CROP_MIN_H", "SINDRI_CROP_MAX_UPSCALE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SINDRI_CROP_PAD", "6")
    from app.eval.runner import _predict_extra
    assert "crop_pad" not in _predict_extra(detect_only=False)


# --- shipping pad 24 --------------------------------------------------------
#
# Measured 2026-09-16 over two doses against r3-awqcontrol (133.93):
#   pad 24 -> 131.87, field_acc 0.5291, escaped_rate 0.2058
#   pad 48 -> 132.73, field_acc 0.5426, escaped_rate 0.2154
# 48 reads more accurately and costs MORE, because three rows moved into
# fully-correct and three into silent errors at w=5. 24 is the setting.

def test_the_default_is_the_measured_setting():
    """Shipping is a default change, not a launch flag: app/main.py and the
    training crop builder both read the resolver, and neither sets an env."""
    assert ex.resolve_crop_knobs({}) == (24, 40, 3.0)


def test_a_default_run_RECORDS_its_knobs():
    """The trap this ordering exists to avoid. `active_crop_knobs` compares
    against the FROZEN 6/40/3.0 baseline, not against whatever the default
    happens to be -- so a run at the new default records its knobs instead of
    looking identical to every dump predicted at pad 6. Comparing against the
    current default would make _reusable_dump reuse pad-6 dumps straight across
    the change being measured."""
    assert ex.active_crop_knobs({}) == {"crop_pad": 24, "crop_min_h": 40,
                                        "crop_max_upscale": 3.0}


def test_a_run_pinned_BACK_to_the_baseline_records_nothing():
    """The other half, and it is what keeps every historical dump reusable: a
    run explicitly set to the old values is byte-identical in RunConfig to the
    dumps taken before the knob existed, because that is exactly what it is."""
    assert ex.active_crop_knobs({"SINDRI_CROP_PAD": "6"}) == {}
    assert ex.resolve_crop_knobs({"SINDRI_CROP_PAD": "6"}) == (6, 40, 3.0)


# --- the height-dependent pad -----------------------------------------------
#
# Measured 2026-09-16 (docs/plans/2026-09-16-crop-height-diagnostic.md §5): the
# pad response is not spread across tall boxes, it is ONE band. Across pads
# 6/24/48 the 120-200 px bucket went 0.391 -> 0.523 -> 0.578, monotone and still
# climbing, while 40-80 was flat (+0.011), >=200 was flat to three decimals, and
# 80-120 gained nothing and gave back what little it had at 48. A single global
# pad therefore has to split the difference, which is why 48 read better
# everywhere it mattered and still cost more.

def test_by_default_there_is_no_height_dependence():
    """Every dump ever taken used one pad for every box. The default must stay
    byte-identical to that, or the knob changes behaviour for runs that never
    asked for it."""
    for h in (10.0, 60.0, 119.0, 120.0, 500.0):
        assert ex.crop_pad_for(h, env={}) == 24


def test_a_tall_box_gets_the_tall_pad():
    env = {"SINDRI_CROP_PAD_TALL": "48"}
    assert ex.crop_pad_for(119.0, env=env) == 24
    assert ex.crop_pad_for(120.0, env=env) == 48      # threshold is inclusive
    assert ex.crop_pad_for(500.0, env=env) == 48


def test_the_threshold_is_settable():
    """120 px is where the responding band starts, but it is a bucket boundary
    rather than a measured knee -- the arm must be able to move it without a
    code change."""
    env = {"SINDRI_CROP_PAD_TALL": "48", "SINDRI_CROP_TALL_H": "200"}
    assert ex.crop_pad_for(150.0, env=env) == 24
    assert ex.crop_pad_for(200.0, env=env) == 48


def test_a_bad_tall_value_loses_the_arm_rather_than_the_measurement():
    for env in ({"SINDRI_CROP_PAD_TALL": "wide"}, {"SINDRI_CROP_TALL_H": "-5"}):
        try:
            ex.crop_pad_for(150.0, env=env)
        except ValueError:
            continue
        raise AssertionError(f"{env} should have been refused")


def test_no_height_dependence_records_nothing_extra():
    """The tall knobs did not exist before 2026-09-16, and their default means
    "one pad for every box" -- which is exactly what every historical dump did.
    Recording them by default would change the RunConfig of a run that behaves
    identically to its predecessors."""
    assert "crop_pad_tall" not in ex.active_crop_knobs({})


def test_a_height_dependent_run_records_BOTH_tall_knobs():
    """All or none, for the reason the first three follow: a dump that recorded
    the pad without the threshold would not say which boxes got it."""
    got = ex.active_crop_knobs({"SINDRI_CROP_PAD_TALL": "48"})
    assert got["crop_pad_tall"] == 48
    assert got["crop_tall_h"] == 120
    assert got["crop_pad"] == 24        # the base knobs still travel with it
