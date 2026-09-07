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
