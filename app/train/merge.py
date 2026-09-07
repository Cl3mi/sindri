"""Folding a LoRA adapter into its base — and the control that makes the result
readable.

PEFT cannot attach an adapter to an AWQ checkpoint at all (autoawq replaces every
q/k/v/o projection with WQLinear_GEMM, which PEFT does not inject into), so the
only way to serve `read-lora-v1` on what production actually runs is to merge it
into bf16 and re-quantise. That produces a checkpoint no committed number was
measured on, which is why the zero-scale control below exists.

Deliberately outside the merge script and free of torch and peft imports, for
the same reason `app.train.dataset` is: the operator's machine has PIL and no
torch, and this is the part with logic worth testing.
"""


def zero_lora_scaling(model) -> int:
    """Scale every LoRA layer's contribution to zero, in place. Returns how many
    layers were zeroed.

    A merge folds `scaling * B @ A` into the base weight, so zeroing the scaling
    makes `merge_and_unload()` a numeric no-op while still travelling the whole
    merge-and-requantise path. That is precisely the control this route needs:
    it isolates "the round trip changed the model" from "the adapter changed the
    model", and only the second is the result.

    Raises when there is nothing to zero. A model with no LoRA layers was never
    wrapped in PeftModel, so quantising it yields a plain-base checkpoint that
    would pass as the control while silently skipping the round trip the control
    exists to measure -- the same class of failure as resolve_adapter falling
    back to the base model under a treatment arm's run name."""
    n = 0
    for module in model.modules():
        scaling = getattr(module, "scaling", None)
        # A dict keyed by adapter name is what a peft LoRA layer carries; other
        # modules that happen to have a `scaling` attribute (a float, say) are
        # not adapters and must not be touched.
        if isinstance(scaling, dict) and scaling:
            for name in scaling:
                scaling[name] = 0.0
            n += 1
    if n == 0:
        raise ValueError(
            "no LoRA layers found, so there is no adapter contribution to zero. "
            "This model was never wrapped in PeftModel: merging it would write a "
            "plain-base checkpoint that reads as the zero-scale control while "
            "skipping the merge round trip the control exists to measure.")
    return n
