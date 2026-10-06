"""The verifier's pure parts: crop, prompt, score. Everything here is FROZEN by
docs/plans/2026-10-07-verifier-registration.md §2 -- a failing test after a
split has been priced means the registration moved, not that the test is
stale."""
import math

import pytest
from PIL import Image

from app.models import Characteristic
from app.pipeline import verifier as vf


# --- §2 crop ---------------------------------------------------------------------

def _page(w=2000, h=1500):
    return Image.new("RGB", (w, h), "white")


def test_context_crop_is_three_times_the_box_each_axis():
    box = (1000, 700, 1100, 740)                 # 100 x 40
    crop, inner = vf.context_crop(_page(), box)
    # 100 wide -> 300; 40 tall -> 120, raised to the 200 px minimum
    assert crop.size == (300, 200)
    # the box sits where the original box was, inside the crop
    assert inner == (100, 80, 200, 120)


def test_context_crop_has_a_200px_minimum_per_axis():
    crop, _ = vf.context_crop(_page(), (1000, 700, 1010, 705))
    assert crop.size == (200, 200)


def test_context_crop_clamps_to_the_page():
    crop, inner = vf.context_crop(_page(), (0, 0, 100, 40))
    assert crop.size[0] <= 300 and crop.size[1] <= 200
    assert inner[0] == 0 and inner[1] == 0


def test_the_box_is_outlined_in_red_and_the_page_is_untouched():
    page = _page()
    crop, inner = vf.context_crop(page, (1000, 700, 1100, 740))
    assert crop.getpixel((inner[0], inner[1] + 10)) == (255, 0, 0)
    assert page.getpixel((1000, 710)) == (255, 255, 255)


# --- §2 prompt -------------------------------------------------------------------

def test_prompt_is_the_registered_text():
    assert vf.VERIFY_PROMPT == (
        "The red rectangle marks one region detected on a technical drawing. "
        "Is the content inside the red rectangle a dimension or tolerance "
        "callout (a measured size with or without tolerance) that a quality "
        "inspector would number with an inspection balloon? Answer with "
        "exactly one word: yes or no.")


def test_prompt_is_outside_the_hashed_read_prompts():
    """Adding the verifier must not move prompt_sha256 -- every existing run's
    comparability depends on it."""
    from app.eval.runner import _prompt_sha256
    assert _prompt_sha256() == "aa7659f1929184ea"


def test_prompt_hash_is_recorded_separately():
    assert len(vf.verifier_prompt_hash()) == 16
    assert vf.verifier_prompt_hash() == vf.verifier_prompt_hash()


# --- §2 score --------------------------------------------------------------------

@pytest.mark.parametrize("p_yes,p_no,expected", [
    (0.9, 0.1, 0.9), (0.3, 0.3, 0.5), (0.02, 0.06, 0.25), (1.0, 0.0, 1.0),
])
def test_score_is_yes_over_yes_plus_no(p_yes, p_no, expected):
    assert vf.verifier_probability(p_yes, p_no) == pytest.approx(expected)


@pytest.mark.parametrize("p_yes,p_no", [
    (0.0, 0.0), (1e-7, 1e-8), (math.nan, 0.5), (0.5, math.inf),
])
def test_score_is_none_when_the_model_did_not_answer(p_yes, p_no):
    """None is never dropped: a verdict the model did not give cannot remove
    a value. A NaN that slipped through would compare False against every
    threshold and silently keep the row anyway, but an explicit None is
    countable."""
    assert vf.verifier_probability(p_yes, p_no) is None


# --- the field --------------------------------------------------------------------

def test_verifier_p_defaults_to_none_so_old_dumps_do_not_claim_a_verdict():
    assert Characteristic(pos=1).verifier_p is None


def test_a_non_finite_verifier_p_is_stored_as_none():
    """A NaN serialises as null and reloads with a crash -- the r3-awqtest
    lesson. Coerced at construction instead."""
    assert Characteristic(pos=1, verifier_p=math.nan).verifier_p is None
    assert Characteristic(pos=1, verifier_p=0.4).verifier_p == 0.4
