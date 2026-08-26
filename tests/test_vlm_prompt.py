import pytest
from PIL import Image

from app.pipeline.ocr import vlm_backend
from app.pipeline.ocr.vlm_backend import _cap_long_edge, _MAX_READ_LONG_EDGE


def test_gdt_prompt_exists_and_is_frame_aware():
    p = vlm_backend._GDT_PROMPT
    assert "feature control frame" in p.lower()
    assert "datum" in p.lower()
    assert "comma" in p.lower()


def test_notes_block_prompt_requests_json_array():
    from app.pipeline.ocr.vlm_backend import _NOTES_PROMPT
    p = _NOTES_PROMPT
    assert "general-notes" in p.lower()
    assert "JSON array" in p            # structured output, not tab-delimited
    assert '"pos"' in p
    assert "comma as the decimal separator" in p.lower()
    assert "no prose" in p.lower()


def test_cap_long_edge_downscales_large_legend_crop():
    # a full legend crop that OOMs the vision encoder at native size
    im = Image.new("RGB", (2890, 1436), "white")
    out = _cap_long_edge(im)
    assert max(out.size) == _MAX_READ_LONG_EDGE
    # aspect ratio preserved
    assert abs(out.size[0] / out.size[1] - 2890 / 1436) < 0.01


def test_cap_long_edge_leaves_small_crops_untouched():
    im = Image.new("RGB", (300, 120), "white")   # a typical callout crop
    out = _cap_long_edge(im)
    assert out.size == (300, 120)
    assert out is im


def test_title_prompt_requests_json_label_value():
    p = vlm_backend._TITLE_PROMPT
    assert "title block" in p.lower()
    assert '"label"' in p and '"value"' in p
    # caption can be above OR below the value (the two-layout requirement)
    assert "above" in p.lower() and "below" in p.lower()


def test_prompt_sha256_is_unchanged_by_the_registry():
    """The comparability proof. Every report from the Rung-0 baseline through the
    four direction-run arms carries prompt_sha256 aa7659f1929184ea; if the
    refactor moves it, no prompt arm can be compared against any of them."""
    from app.eval.runner import _prompt_sha256
    assert _prompt_sha256() == "aa7659f1929184ea"


def test_read_and_detect_prompts_default_to_base():
    assert vlm_backend.read_prompt(env={}) == vlm_backend._PROMPT
    assert vlm_backend.detect_prompt(env={}) == vlm_backend._DETECT_PROMPT


def test_unknown_variant_name_fails_loudly_instead_of_using_base():
    """A typo in an arm's env must lose the arm, not silently produce a control
    run wearing a treatment arm's name."""
    with pytest.raises(ValueError, match="SINDRI_READ_PROMPT"):
        vlm_backend.read_prompt(env={"SINDRI_READ_PROMPT": "typo"})


def test_active_prompts_names_the_variant_for_run_config_extra():
    assert vlm_backend.active_prompts(env={}) == {"read_prompt": "base",
                                                  "detect_prompt": "base"}


def test_selecting_a_variant_changes_the_effective_prompt_hash(monkeypatch):
    """What makes a prompt arm attributable: the hash must move with the variant,
    not with the file."""
    import hashlib
    monkeypatch.setitem(vlm_backend._READ_VARIANTS, "probe", "a different prompt")
    base = hashlib.sha256("\n".join(
        vlm_backend.effective_prompts(env={})).encode()).hexdigest()[:16]
    other = hashlib.sha256("\n".join(vlm_backend.effective_prompts(
        env={"SINDRI_READ_PROMPT": "probe"})).encode()).hexdigest()[:16]
    assert base != other
