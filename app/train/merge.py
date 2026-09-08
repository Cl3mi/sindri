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


def assert_quantisable(model_type: str, supported) -> None:
    """Refuse, before the 137 GB load, a model autoawq will not be able to
    quantise afterwards.

    The merge half succeeds for any architecture -- load, fold, save all work --
    so an unsupported model fails only once quantisation starts, hours in and
    after ~137 GB has been written. autoawq 0.2.8 ships a `qwen2_vl` wrapper and
    no `qwen2_5_vl` one, while this corpus' base is `qwen2_5_vl`; 0.2.9 adds it,
    which is why quantisation runs in its own image and serving does not move.

    SystemExit rather than ValueError: this is a CLI preflight, and it reports a
    misconfigured run rather than a bug in a caller."""
    if model_type not in set(supported):
        raise SystemExit(
            f"autoawq here cannot quantise model_type {model_type!r}. It "
            f"supports: {sorted(set(supported))}. Merging first would spend the "
            f"load, the fold and the save before failing. Build the "
            f"quantisation image (Dockerfile.quant, autoawq>=0.2.9) and run "
            f"there; the pinned serving image must NOT move.")


# Calibration settings for the AWQ pass. CONSTANTS, deliberately not CLI flags:
# the arm and its zero-scale control must be quantised identically or the delta
# measures the calibration instead of the adapter, and a flag is exactly how the
# two would drift apart.
#
# autoawq's default n_parallel_calib_samples is None, which forwards all 128
# calibration samples in ONE pass. On a 72B that OOM'd both cards ~20 minutes in
# (9.22 GiB wanted, 6.24 GiB free, plus 14.75 GiB reserved-but-unallocated).
# Chunking to 8 cuts that activation ~16x and leaves a wide margin; it changes
# throughput, not the objective being optimised.
CALIB = {"n_parallel_calib_samples": 8,
         "max_calib_samples": 128,
         "max_calib_seq_len": 512}


def check_merge_target(out, quantise_only: bool) -> None:
    """Decide whether `out` may be written, or must already hold a merge.

    Two different failures, and only one of them is about overwriting:

    * a fresh merge into a populated directory silently mixes the arm with its
      control, which differ in nothing a directory listing shows;
    * a --quantise-only retry into an EMPTY directory would quantise nothing and
      still write a checkpoint under the arm's name.

    The retry path exists because the merge writes 137 GB in minutes while
    quantisation runs for hours -- when the latter fails, refusing to reuse the
    former costs the whole merge again for nothing."""
    populated = out.is_dir() and any(out.iterdir())
    if quantise_only:
        if not populated:
            raise SystemExit(
                f"no merged checkpoint at {out} to quantise. --quantise-only "
                f"resumes a failed quantisation; it cannot create the merge it "
                f"needs, and writing an empty result under this name would pass "
                f"as the real checkpoint.")
        return None
    if populated:
        raise SystemExit(
            f"{out} is not empty. Refusing to write a checkpoint over another "
            f"one: the arm and its zero-scale control differ in nothing a "
            f"directory listing shows, and mixing them is unrecoverable. To "
            f"resume a failed quantisation over this merge, pass "
            f"--quantise-only.")
    return None
