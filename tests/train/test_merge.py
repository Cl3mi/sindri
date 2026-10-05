"""The zero-scale control for the merge-and-requantise route.

Merging `read-lora-v1` into bf16 and re-quantising to AWQ produces a DIFFERENT
checkpoint from the one every AWQ number was measured on, so a delta against
r3-awqcontrol would confound the adapter with the round trip through
merge + autoawq. The control is the same round trip with the adapter's
contribution scaled to zero: numerically the base, but through byte-identical
code. If it does not reproduce 170.05, the round trip moved the model and no
delta from this route means anything.

Duck-typed rather than built on peft, for the reason app.train.dataset exists:
the operator's machine has PIL and no torch.
"""
import pytest

from app.train.merge import zero_lora_scaling


class _LoraLayer:
    """A peft LoRA layer: what identifies one here is that it carries a
    per-adapter `scaling` map, which is exactly what the merge multiplies by."""

    def __init__(self, scaling):
        self.scaling = dict(scaling)


class _Plain:
    """Any other module in the tree. Must come through untouched."""

    def __init__(self):
        self.weight = "unchanged"


class _Model:
    def __init__(self, mods):
        self._mods = mods

    def modules(self):
        return iter(self._mods)


def test_every_lora_layer_is_scaled_to_zero():
    """A merge multiplies each adapter's delta by its scaling before folding it
    into the base weight, so zeroing every scaling makes merge_and_unload a
    numeric no-op while still running the whole path."""
    layers = [_LoraLayer({"default": 2.0}), _LoraLayer({"default": 0.5})]
    model = _Model(layers + [_Plain()])

    assert zero_lora_scaling(model) == 2
    assert all(v == 0.0 for l in layers for v in l.scaling.values())


def test_a_layer_carrying_several_adapters_is_zeroed_in_all_of_them():
    """One residual non-zero scaling is one live adapter, and the control would
    quietly become a treatment arm."""
    layer = _LoraLayer({"default": 2.0, "other": 1.0})

    zero_lora_scaling(_Model([layer]))

    assert layer.scaling == {"default": 0.0, "other": 0.0}


def test_modules_that_are_not_lora_layers_are_left_alone():
    plain = _Plain()

    zero_lora_scaling(_Model([_LoraLayer({"default": 1.0}), plain]))

    assert plain.weight == "unchanged"


def test_a_model_with_no_lora_layers_fails_loudly():
    """The whole point of the control is that it travels the adapter's path.
    A model with nothing to zero was never wrapped in PeftModel, so quantising
    it would produce a plain-base checkpoint that LOOKS like the control and
    silently drops the round trip the control exists to measure."""
    with pytest.raises(ValueError, match="no LoRA layers"):
        zero_lora_scaling(_Model([_Plain(), _Plain()]))


# --- quantisability preflight ------------------------------------------------

from app.train.merge import assert_quantisable


def test_a_model_autoawq_cannot_quantise_is_refused_before_the_merge():
    """The 137 GB load, the merge and the save all succeed for a model autoawq
    then cannot touch, so the failure lands hours in. autoawq 0.2.8 ships a
    qwen2_vl wrapper but no qwen2_5_vl one, and the base here is qwen2_5_vl --
    which is exactly the run this check exists to not waste."""
    with pytest.raises(SystemExit, match="qwen2_5_vl"):
        assert_quantisable("qwen2_5_vl", ["llama", "qwen2", "qwen2_vl"])


def test_the_supported_types_are_named_so_the_fix_is_obvious():
    """Naming what IS supported is what turns this from "it broke" into "you
    need the 0.2.9 wrapper"."""
    with pytest.raises(SystemExit, match="qwen2_vl"):
        assert_quantisable("qwen2_5_vl", ["qwen2_vl"])


def test_a_supported_model_passes_quietly():
    assert assert_quantisable("qwen2_5_vl",
                              ["qwen2_vl", "qwen2_5_vl"]) is None


# --- resuming after a failed quantisation ------------------------------------
# The merge writes 137 GB and takes minutes; quantisation takes hours and is
# what OOM'd. Both merged checkpoints survived intact, so a retry must reuse
# them -- but preflight refuses a non-empty --out, which would abort the retry
# and force 137 GB to be written again for nothing.

from app.train.merge import check_merge_target


def test_an_existing_checkpoint_blocks_a_fresh_merge(tmp_path):
    """Unchanged: the arm and its zero-scale control differ in nothing a
    directory listing shows, so silently merging over one is unrecoverable."""
    out = tmp_path / "zero-scale"
    out.mkdir()
    (out / "model-00001-of-00031.safetensors").write_text("x")

    with pytest.raises(SystemExit, match="not empty"):
        check_merge_target(out, quantise_only=False)


def test_quantise_only_REQUIRES_the_merged_checkpoint_to_be_there(tmp_path):
    """Resuming into an empty directory would quantise nothing and write a
    checkpoint named as if it were the arm."""
    with pytest.raises(SystemExit, match="no merged checkpoint"):
        check_merge_target(tmp_path / "absent", quantise_only=True)


def test_quantise_only_accepts_the_checkpoint_the_failed_run_left(tmp_path):
    """The whole point of the retry: reuse the 137 GB that is already on disk."""
    out = tmp_path / "zero-scale"
    out.mkdir()
    (out / "model-00001-of-00031.safetensors").write_text("x")

    assert check_merge_target(out, quantise_only=True) is None


def test_a_fresh_merge_into_an_empty_directory_is_fine(tmp_path):
    assert check_merge_target(tmp_path / "new", quantise_only=False) is None


# --- two measured OOM/shape failures, pinned --------------------------------

from app.train.merge import AWQ_LOAD, CALIB


def test_the_model_is_not_loaded_onto_the_card_it_is_quantised_on():
    """The real cause of the first OOM. autoawq's from_pretrained defaults to
    device_map="auto", which put ~57.5 GiB of the 72B on the card and left only
    ~21 GiB for calibration activations. Its quantizer moves each block to the
    device itself (quantize/quantizer.py:137), so the model belongs on CPU and
    the card stays free -- the host has 1007 GB of RAM for exactly this."""
    assert AWQ_LOAD["device_map"] == "cpu"


def test_calibration_is_not_chunked():
    """n_parallel_calib_samples splits the hidden states but NOT the mrope
    position embeddings, so Qwen2.5-VL dies in
    apply_multimodal_rotary_pos_emb with "tensor a (8) must match tensor b
    (59)". Chunking is not available on this architecture, and it is not needed
    once the model is off the card."""
    assert CALIB.get("n_parallel_calib_samples") is None


def test_calibration_keeps_autoawq_defaults():
    """Cutting samples or sequence length degrades the scale estimates, and the
    zero-scale control has to reproduce a checkpoint Qwen calibrated properly.
    Fix the memory by placement, not by weakening calibration."""
    assert CALIB["max_calib_samples"] == 128
    assert CALIB["max_calib_seq_len"] == 512


# --- returning each block to CPU after it is quantised -----------------------
# quantizer.py:129-137 moves a CPU-resident block onto the card and never moves
# it back, so with device_map="cpu" the card accumulates ~2.6 GiB per block.
# Measured on both cards: 40.7 GiB at block 1, 69.9 GiB at block 12, against a
# 79.1 GiB capacity. autoawq takes a quantizer_cls, which is the supported way
# to change this.

from app.train.merge import offloading_quantizer


class _Block:
    def __init__(self):
        self.device = "cuda:0"

    def to(self, device):
        self.device = device
        return self


class _FakeQuantizer:
    """Stands in for awq.quantize.quantizer.AwqQuantizer, which cannot be
    imported here -- awq is only in the quantisation image."""

    def __init__(self):
        self.quantised = []

    def _apply_quant(self, module, named_linears):
        self.quantised.append(module)


def test_the_block_is_returned_to_cpu_once_it_has_been_quantised():
    """_apply_quant is the last step the loop takes on a block; everything after
    it works on the next one. Leaving it resident is what fills the card."""
    q = offloading_quantizer(_FakeQuantizer)()
    block = _Block()

    q._apply_quant(block, {})

    assert block.device == "cpu"


def test_the_block_is_still_actually_quantised():
    """An override that forgot super() would offload un-quantised blocks and
    write out a checkpoint that is merely the base in AWQ packaging -- which
    would pass every structural check and silently be the wrong model."""
    q = offloading_quantizer(_FakeQuantizer)()
    block = _Block()

    q._apply_quant(block, {})

    assert q.quantised == [block]


def test_quantisation_happens_before_the_offload_not_after():
    """Offloading first would quantise on CPU: astronomically slow, and
    _apply_quant explicitly moves layers to the best device anyway."""
    order = []

    class _Recording(_FakeQuantizer):
        def _apply_quant(self, module, named_linears):
            order.append("quantise")
            super()._apply_quant(module, named_linears)

    class _RecordingBlock(_Block):
        def to(self, device):
            order.append(f"to:{device}")
            return super().to(device)

    offloading_quantizer(_Recording)()._apply_quant(_RecordingBlock(), {})

    assert order == ["quantise", "to:cpu"]


# --- the AWQ recipe for route A, via llm-compressor --------------------------

from app.train.merge import AWQ_IGNORE, awq_scheme


def test_the_vision_tower_is_left_alone():
    """Qwen ships the official AWQ checkpoint with the vision tower unquantised.
    Quantising it here would make our checkpoint structurally different from the
    one 170.05 was measured on, so r3-mergedcontrol would be measuring that
    difference rather than the merge round trip it exists to price."""
    assert any("visual" in pattern for pattern in AWQ_IGNORE)


def test_the_output_head_is_left_alone():
    """lm_head is excluded from AWQ by convention and by Qwen's own checkpoint;
    quantising it costs accuracy for almost no memory."""
    assert "lm_head" in AWQ_IGNORE


def test_the_scheme_matches_what_production_already_serves():
    """4-bit, group 128 — the same quantisation r3-awqcontrol runs on. Any
    departure is a second variable on top of the merge, and the arm could not be
    attributed to the adapter."""
    scheme = awq_scheme()
    assert scheme["num_bits"] == 4
    assert scheme["group_size"] == 128
