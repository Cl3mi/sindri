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


def test_center_variant_names_the_centre_callout_and_leaves_base_alone():
    """The arm's hypothesis, pinned as text. Phase A: 64 of 144 misread rows sit
    on misplaced pairs and 87.5% of misplaced pairs are wrong, which is the
    signature of transcribing a neighbouring callout correctly. The base prompt
    says "ONLY the dimension" for a crop that may hold several, and never says
    which one."""
    from app.eval.runner import _prompt_sha256
    p = vlm_backend._READ_VARIANTS["center"]
    assert "centre" in p.lower() or "center" in p.lower()
    assert "neighbour" in p.lower() or "neighbouring" in p.lower()
    assert p != vlm_backend._READ_VARIANTS["base"]
    # base untouched: it is the comparison point for every committed report
    assert _prompt_sha256() == "aa7659f1929184ea"


def test_center_variant_is_selectable_and_moves_the_hash():
    from app.eval.runner import _prompt_sha256
    assert (vlm_backend.read_prompt(env={"SINDRI_READ_PROMPT": "center"})
            == vlm_backend._READ_VARIANTS["center"])
    assert vlm_backend.active_prompts(
        env={"SINDRI_READ_PROMPT": "center"})["read_prompt"] == "center"
    import hashlib
    h = hashlib.sha256("\n".join(vlm_backend.effective_prompts(
        env={"SINDRI_READ_PROMPT": "center"})).encode()).hexdigest()[:16]
    assert h != _prompt_sha256()


def test_box_variant_demands_a_complete_tight_box_and_leaves_base_alone():
    """The arm's hypothesis, pinned as text. Phase A: 49 rows (25% of the wrong
    rows) have ALL FOUR fields wrong and 42% have the whole value wrong, which
    is what a box cutting through or overrunning the callout produces. The base
    detect prompt never states what the box must enclose."""
    from app.eval.runner import _prompt_sha256
    p = vlm_backend._DETECT_VARIANTS["box"]
    assert "tolerance" in p.lower()
    assert "JSON array" in p          # still structured output, parser unchanged
    assert '"box"' in p and '"kind"' in p
    assert p != vlm_backend._DETECT_VARIANTS["base"]
    assert _prompt_sha256() == "aa7659f1929184ea"


def test_box_variant_offers_the_same_kind_vocabulary_as_base():
    """parse_detections validates kind against detect._KINDS and drops anything
    else, so a variant that renames or omits one of the kinds the VLM is asked
    for would silently discard detections rather than improve them.

    Compared against BASE, not against _KINDS: `theoretical` is in _KINDS but is
    assigned by the CV box detector (boxes._box_kind), never by the VLM, so the
    base prompt does not offer it either. The invariant is "the variant asks for
    the same set base asks for"."""
    import re

    def kinds(prompt):
        m = re.search(r'"kind":"([a-z|]+)"', prompt)
        assert m, f"no kind alternation found in {prompt[:60]!r}"
        return set(m.group(1).split("|"))

    base = kinds(vlm_backend._DETECT_VARIANTS["base"])
    assert kinds(vlm_backend._DETECT_VARIANTS["box"]) == base
    # and every kind it does offer must be one parse_detections accepts
    from app.pipeline.detect import _KINDS
    assert base <= _KINDS


def test_box_variant_is_selectable_and_moves_the_hash():
    from app.eval.runner import _prompt_sha256
    assert vlm_backend.active_prompts(
        env={"SINDRI_DETECT_PROMPT": "box"})["detect_prompt"] == "box"
    import hashlib
    h = hashlib.sha256("\n".join(vlm_backend.effective_prompts(
        env={"SINDRI_DETECT_PROMPT": "box"})).encode()).hexdigest()[:16]
    assert h != _prompt_sha256()


def test_active_quant_is_none_by_default():
    """Every measurement to date ran the AWQ checkpoint's own quantisation. That
    must stay the default, or the frozen 174.30 baseline stops being reproducible."""
    assert vlm_backend.active_quant(env={}) is None


def test_active_quant_reports_nf4_when_selected():
    assert vlm_backend.active_quant(env={"SINDRI_QUANT": "nf4"}) == "nf4"


def test_an_unsupported_quant_fails_loudly_instead_of_falling_back():
    """Same rule as the prompt variants and the adapter. Falling back to the
    checkpoint default would serve a DIFFERENT base than the adapter was trained
    against, and report the result as the fine-tune's."""
    with pytest.raises(ValueError, match="SINDRI_QUANT"):
        vlm_backend.active_quant(env={"SINDRI_QUANT": "int8"})


def test_active_adapter_is_none_by_default():
    assert vlm_backend.active_adapter(env={}) is None


def test_an_adapter_name_is_reported_for_run_config_extra():
    assert vlm_backend.active_adapter(
        env={"SINDRI_ADAPTER": "read-lora-v1"}) == "read-lora-v1"


def test_an_adapter_that_does_not_exist_fails_loudly(tmp_path, monkeypatch):
    """A typo must lose the arm, not silently serve the base model under a
    treatment arm's run name -- which would read as "the LoRA had no effect" for
    a run that never loaded it."""
    monkeypatch.setattr(vlm_backend, "_ADAPTER_ROOT", tmp_path)
    with pytest.raises(ValueError, match="read-lora-typo"):
        vlm_backend.resolve_adapter(env={"SINDRI_ADAPTER": "read-lora-typo"})


def test_an_adapter_that_exists_resolves_to_its_path(tmp_path, monkeypatch):
    monkeypatch.setattr(vlm_backend, "_ADAPTER_ROOT", tmp_path)
    (tmp_path / "read-lora-v1").mkdir()
    (tmp_path / "read-lora-v1" / "adapter_config.json").write_text("{}")
    assert vlm_backend.resolve_adapter(
        env={"SINDRI_ADAPTER": "read-lora-v1"}) == tmp_path / "read-lora-v1"


def test_resolve_adapter_is_none_when_none_was_asked_for(tmp_path, monkeypatch):
    monkeypatch.setattr(vlm_backend, "_ADAPTER_ROOT", tmp_path)
    assert vlm_backend.resolve_adapter(env={}) is None
