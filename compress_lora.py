"""Quantise one already-merged bf16 checkpoint to AWQ. Route A, via
llm-compressor.

The merge half is done and is NOT redone here. Two complete bf16 Qwen2.5-VL-72B
checkpoints already sit in the `sindri-models` volume -- `merged/zero-scale`
(the adapter folded in with its contribution scaled to zero: the CONTROL) and
`merged/read-lora-v1` (the ARM) -- 137 GB and 31 shards each. This script takes
exactly one of them and writes the AWQ checkpoint that gets served.

Why llm-compressor and not autoawq, which `merge_lora.py` used: autoawq has no
offload path. Its quantizer moves each decoder block onto the card and never
moves it back -- measured at ~2.6 GiB per block, OOM at block 18 of 80 -- and
`offloading_quantizer` only postponed the problem. llm-compressor's `oneshot`
takes `sequential_targets` and `sequential_offload_device`, which partition the
model into one subgraph per decoder layer and onload only the live one. That is
the whole reason this file exists; a version without it does not fit.

Touches NO client data. Calibration is `mit-han-lab/pile-val-backup`, the same
public corpus autoawq calibrates on by default.

Run it TWICE, once per checkpoint, and change nothing in between: the arm and
its control must differ only in which 137 GB directory went in. Everything that
could differ is a constant below rather than a flag, and the JSON header prints
all of it so the two logs can be diffed.

Prints counts and paths only.
"""
import argparse
import json
import sys
from pathlib import Path

from app.train.merge import AWQ_IGNORE, awq_scheme

# What the calibration set IS. CONSTANTS, deliberately not CLI flags: the arm
# and its zero-scale control are two runs of this script hours apart, and a flag
# is exactly how the two would drift into measuring the calibration instead of
# the adapter. Same discipline as app.train.merge.CALIB.
#
#   dataset/split -- autoawq's own default calibration corpus, already in the
#     volume's HF cache. A hub id and never a local path: a local path here is
#     how the client corpus would reach an innocuous-looking constant.
#   pool -- how many rows are TOKENISED. llm-compressor tokenises the whole
#     dataset before the sampler ever selects `samples` from it, and pile-val
#     has 214,670 rows, so the slice is what keeps that step to seconds.
#   shuffle -- False. The shuffled path is `RandomSampler(dataset,
#     num_samples=...)` with no generator, i.e. torch's global RNG, so two
#     processes draw two different calibration sets. Unshuffled gives
#     LengthAwareSampler: the `samples` LONGEST rows of the pool, a pure
#     function of the data. That also fills all 512 positions, which is what
#     autoawq got by filtering pile-val to samples over 512 tokens.
#   samples/max_seq_length -- autoawq's defaults, restated. Cutting either
#     degrades the scale estimates, and the control has to stand against a
#     checkpoint Qwen calibrated properly.
#   pad_to_max_length -- False. At batch_size 1 padding buys nothing and
#     calibrating on pad tokens estimates scales for activations the model
#     never sees at inference.
CALIBRATION = {"dataset": "mit-han-lab/pile-val-backup",
               "split": "validation",
               "text_column": "text",
               "pool": 1024,
               "samples": 128,
               "max_seq_length": 512,
               "shuffle": False,
               "pad_to_max_length": False}

# One decoder layer on the card at a time. Qwen2.5-VL-72B has 80 of them, so
# this is 80 subgraphs and the card holds one; without it the pipeline traces
# the model as a single graph and a 137 GB model has to be resident.
#
# `Qwen2_5_VLVisionBlock` is the other entry in the architecture's
# `_no_split_modules` and is deliberately left out: the vision tower is in
# AWQ_IGNORE, so partitioning it would add subgraphs and quantise nothing.
SEQUENTIAL_TARGETS = ["Qwen2_5_VLDecoderLayer"]

# Where everything not currently being quantised waits. The card is 80 GB and
# the model is 137 GB, so "not the card" is the only option; the host has
# 1007 GB of RAM for exactly this.
OFFLOAD_DEVICE = "cpu"

# The only architecture this file is written for. Every pattern below names a
# Qwen2.5-VL module.
ARCHITECTURE = "Qwen2_5_VLForConditionalGeneration"

# The AWQ smoothing map: which activation to scale, and which weights absorb the
# inverse. Passed EXPLICITLY even though llm-compressor registers an entry for
# this architecture, because that entry is wrong here.
#
# `match_modules_set` walks `named_modules()` in order and closes a set when the
# parent context changes. Qwen2.5-VL's 32 vision blocks use a SwiGLU MLP whose
# projections are named gate_proj / up_proj / down_proj -- the same names as the
# decoder's -- and they come first in module order, so the registry's unscoped
# `re:.*up_proj$` sweeps them in. Measured against the real 72B config:
#
#   post_attention_layernorm -> gate/up   1 set instead of 80, holding all 80
#                                         layernorms; llm-compressor raises
#                                         "AWQ needs to match a single
#                                         smoothlayer for each mapping"
#   up_proj -> down_proj                112 sets instead of 80, the extra 32
#                                         being the vision tower -- and this one
#                                         does NOT raise
#
# The second is the reason for scoping rather than just working around the
# crash: AWQ_IGNORE keeps the tower unquantised so our checkpoint matches the
# official Qwen AWQ one, and smoothing it anyway would move it regardless.
#
# The four pairs themselves are unchanged from what llm-compressor registered.
#
# Three of them smooth. The v_proj -> o_proj pair resolves 80 sets and produces
# none: this model is GQA (64 query heads, 8 key/value), so v_proj is 1024 wide
# against o_proj's 8192 and `_check_layers_are_compatible` drops every set. It
# is kept anyway because autoawq excludes the same pairing on the same grounds,
# so dropping it here is what reproduces Qwen's own AWQ checkpoint rather than a
# departure from it. check_smoothing_survives is what makes that visible instead
# of assumed.

# q, k, v, o, gate, up, down. What AWQ_IGNORE is supposed to leave standing once
# the vision tower and lm_head are excluded, and the number that says it did.
LINEARS_PER_DECODER_LAYER = 7

AWQ_MAPPINGS = [
    ("re:.*language_model.*input_layernorm$",
     ["re:.*language_model.*q_proj$", "re:.*language_model.*k_proj$",
      "re:.*language_model.*v_proj$"]),
    ("re:.*language_model.*v_proj$", ["re:.*language_model.*o_proj$"]),
    ("re:.*language_model.*post_attention_layernorm$",
     ["re:.*language_model.*gate_proj$", "re:.*language_model.*up_proj$"]),
    ("re:.*language_model.*up_proj$", ["re:.*language_model.*down_proj$"]),
]


def preflight(checkpoint: Path, out: Path) -> dict:
    """Everything checkable before the 137 GB load, and the facts the two runs
    are compared on.

    Ordered cheapest-first and deliberately paranoid: the arm and the control
    are two directories whose names are the only thing that tells them apart,
    and a wrong one here is not detectable in the result -- an AWQ checkpoint
    built from the control looks exactly like one built from the arm."""
    if not checkpoint.is_dir():
        raise SystemExit(
            f"no merged checkpoint at {checkpoint}. This script quantises an "
            f"existing bf16 merge and cannot make one; merge_lora.py does that.")

    config = checkpoint / "config.json"
    if not config.is_file():
        raise SystemExit(
            f"{checkpoint} has no config.json, so it is not a model checkpoint. "
            f"An ADAPTER directory looks like this and carries safetensors too "
            f"-- /models/adapters/read-lora-v1 is one path component away from "
            f"/models/merged/read-lora-v1.")

    index = checkpoint / "model.safetensors.index.json"
    if not index.is_file():
        raise SystemExit(f"{checkpoint} has no model.safetensors.index.json, so "
                         f"the shards it should hold cannot be reconciled.")

    # Reconcile the shards on disk against the ones the index names. The merge
    # writes 137 GB over minutes onto a host that has dropped an ssh channel
    # mid-run; a short checkpoint still loads far enough to start quantising and
    # then fails hours in, with the whole calibration pass wasted.
    weight_map = json.loads(index.read_text())["weight_map"]
    named = sorted(set(weight_map.values()))
    missing = [shard for shard in named if not (checkpoint / shard).is_file()]
    if missing:
        raise SystemExit(
            f"{checkpoint} is incomplete: the index names {len(named)} shards "
            f"and {len(missing)} are not on disk, starting with {missing[0]}. "
            f"Re-run the merge rather than quantising a partial checkpoint.")

    # Last, because it is the check whose failure costs nothing to recover from.
    from app.train.merge import check_merge_target
    check_merge_target(out, quantise_only=False)

    architectures = json.loads(config.read_text()).get("architectures") or [None]
    return {"checkpoint": str(checkpoint),
            "out": str(out),
            "shards": len(named),
            "tensors": len(weight_map),
            "architecture": architectures[0]}


def check_architecture(architecture: str) -> None:
    """Refuse anything but the architecture the constants above describe.

    SystemExit rather than ValueError, for the same reason as
    merge.assert_quantisable: this reports a misconfigured run, not a bug in a
    caller."""
    if architecture != ARCHITECTURE:
        raise SystemExit(
            f"this script quantises {ARCHITECTURE} and the checkpoint says "
            f"{architecture!r}. The smoothing map, the ignore list and the "
            f"sequential target all name Qwen2.5-VL modules; against another "
            f"architecture they would resolve nothing and llm-compressor would "
            f"still write a checkpoint.")
    return None


def check_mapping_coverage(counts: dict, layers: int) -> None:
    """Refuse unless every smoothing mapping resolves to exactly one set per
    decoder layer.

    This is the check that would have caught the registry map, and it is worth
    its weight because AWQ's two ways of getting this wrong pull in opposite
    directions and only one of them is loud:

    * too many sets means modules outside the language model were swept in --
      the vision tower, which must stay exactly as Qwen ships it;
    * too few, or zero, means the map has gone stale against the module layout
      and `match_modules_set` returned silently. AWQ then smooths nothing and
      still writes a complete 4-bit checkpoint, which is indistinguishable from
      a good one until it is scored.

    The two guards before that comparison are there because a per-entry check
    over nothing reports success: no counts at all, and no layers to compare
    them against, are both the quiet failure wearing the guard's own clothes.

    Cheap enough to run in --dry-run: the counts come from the architecture
    built on the meta device, which reads no weights at all. Resolving is not
    smoothing, though -- check_smoothing_survives is the half of this that
    llmcompressor's own filters decide."""
    # Before the per-entry comparison, because both of these make it VACUOUS --
    # there is nothing for it to object to, and it reports success. `layers` is
    # the denominator of check_quantised_scope too, so at zero both guards agree
    # that nothing smoothed and nothing quantised is exactly right.
    if layers < 1:
        raise SystemExit(
            f"no decoder layers were found ({layers}), so there is nothing to "
            f"reconcile these counts against. Every mapping resolving to zero "
            f"sets would then agree with zero layers and the quantised-scope "
            f"check would agree that 0 == 0 x {LINEARS_PER_DECODER_LAYER}: both "
            f"guards pass and AWQ writes a checkpoint it neither smoothed nor "
            f"quantised. resolve_on_meta reads this off "
            f"model.model.language_model.layers.")

    # The same vacuum from the other side: no entries, nothing to object to.
    if not counts:
        raise SystemExit(
            f"no smoothing mapping was resolved at all, against "
            f"{len(AWQ_MAPPINGS)} in AWQ_MAPPINGS. AWQ's failure mode for an "
            f"empty map is to smooth nothing and still write a complete 4-bit "
            f"checkpoint, so this is the loudest this can be made.")

    wrong = {pattern: n for pattern, n in counts.items() if n != layers}
    if wrong:
        raise SystemExit(
            f"the AWQ smoothing map does not resolve to one set per decoder "
            f"layer ({layers} of them): {wrong}. More sets than layers means "
            f"the vision tower was swept in; fewer means the map no longer "
            f"matches this transformers' module names, and AWQ would smooth "
            f"nothing while still writing a checkpoint.")

    # Last, because the per-entry check above says more about a map that IS
    # there. Counts are keyed by the smooth pattern, so a missing entry is
    # either a mapping that was never resolved or two that collapsed onto one
    # key -- and the collapsed one is never checked by anything.
    if len(counts) != len(AWQ_MAPPINGS):
        raise SystemExit(
            f"the coverage check got {len(counts)} counts for "
            f"{len(AWQ_MAPPINGS)} mappings, so one was never resolved or two "
            f"share a smooth layer and collapsed onto one key. An unchecked "
            f"mapping is one AWQ may silently smooth nothing for.")
    return None


def check_smoothing_survives(smoothed: dict, layers: int) -> None:
    """Refuse unless the mappings that resolved will actually smooth something.

    Resolving a set and smoothing it are two different things, and
    check_mapping_coverage only sees the first. llmcompressor resolves a set and
    then drops it if the smooth layer's output width does not match the balance
    layer's input width (`_check_layers_are_compatible`) or if nothing in the
    set is targeted for quantisation -- the first at `logger.debug` per set with
    one aggregate warning, the second at `logger.warning`, both buried in a
    calibration log thousands of lines long.

    A mapping dropped for ALL layers is tolerated, because one is, correctly:
    Qwen2.5-VL-72B is GQA, v_proj is 1024 wide against o_proj's 8192, and
    v_proj -> o_proj is dropped on all 80 layers. autoawq excludes that same
    pairing, so Qwen's own AWQ checkpoint -- the one r3-awqcontrol's 170.05 was
    measured on -- was built without it too.

    What is refused is a mapping surviving on SOME layers, which quantises the
    decoder to two different qualities down its own depth, and a map where
    nothing survives at all, which is 4-bit with no smoothing: the exact
    degradation AWQ exists to prevent, in a checkpoint that is complete,
    correctly shaped, and only measurable hours later in review cost."""
    partial = {pattern: n for pattern, n in smoothed.items()
               if n not in (0, layers)}
    if partial:
        raise SystemExit(
            f"these smoothing mappings survive on some decoder layers and not "
            f"others ({layers} layers): {partial}. llmcompressor skips a set "
            f"whose shapes do not line up at debug level, so the layers that "
            f"lost would be quantised unsmoothed while their neighbours were "
            f"not, and nothing downstream reports it.")

    if not any(n == layers for n in smoothed.values()):
        raise SystemExit(
            f"every smoothing mapping was dropped, so AWQ would smooth nothing "
            f"and quantise to 4 bits regardless: {smoothed}. The checkpoint "
            f"would still be complete and correctly shaped -- the only symptom "
            f"is review cost, ~9 hours after this point.")
    return None


def check_quantised_scope(quantised: int, layers: int) -> None:
    """Refuse unless AWQ_IGNORE left exactly the decoder's linears standing.

    AWQ_IGNORE is a tuple of patterns; this is the check that they still MATCH.
    It carries two spellings of the vision tower ("re:visual.*" and
    "re:model.visual.*") because the checkpoint on disk was written by
    transformers 4.51.3 under the old layout while the image that reads it is
    on 5.x and renames `visual` to `model.visual`. One more rename and only one
    of those patterns would hit -- silently, since an ignore pattern that
    matches nothing is not an error.

    What it would cost: Qwen ships Qwen2.5-VL-72B-Instruct-AWQ with the tower
    unquantised and r3-awqcontrol's 170.05 was measured on that checkpoint, so
    a quantised tower makes ours structurally different and r3-mergedcontrol
    prices that instead of the merge round trip it exists to measure."""
    expected = layers * LINEARS_PER_DECODER_LAYER
    if quantised != expected:
        raise SystemExit(
            f"the recipe would quantise {quantised} Linear modules; "
            f"{layers} decoder layers x {LINEARS_PER_DECODER_LAYER} is "
            f"{expected}. More means AWQ_IGNORE no longer matches the vision "
            f"tower or lm_head under this transformers' module names; fewer "
            f"means it now matches part of the decoder.")
    return None


def quantization_kwargs() -> dict:
    """The QuantizationModifier arguments, built from what the merge module
    already pinned.

    Imported rather than restated: a second copy of either list would drift, and
    both drifts are invisible in the output. Quantising the vision tower makes
    our checkpoint structurally different from the official Qwen AWQ one that
    r3-awqcontrol's 170.05 was measured on, and any departure from 4-bit
    group-128 is a second variable on top of the merge."""
    return {"targets": ["Linear"],
            "ignore": list(AWQ_IGNORE),
            # config_groups rather than the "W4A16_ASYM" preset: the preset
            # happens to carry the same four values today, but awq_scheme() is
            # the one the merge route pins and the one a reader can check.
            "config_groups": {"group_0": {"targets": ["Linear"],
                                          "weights": awq_scheme()}}}


def awq_mappings():
    """AWQ_MAPPINGS as the objects llm-compressor wants."""
    from llmcompressor.modifiers.transform.awq import AWQMapping
    return [AWQMapping(smooth_layer=smooth, balance_layers=balance)
            for smooth, balance in AWQ_MAPPINGS]


def resolve_on_meta(checkpoint: Path) -> dict:
    """Count what the recipe will actually touch, without reading a byte of the
    137 GB.

    The meta device allocates no storage, so building the 72B here is seconds
    and needs neither the shards nor a card -- which is what lets the whole
    recipe be validated in --dry-run instead of an hour into the real run.

    Two counts per mapping, because they differ and only the second is what AWQ
    does: `mapping_sets` is what `match_modules_set` resolves, `smoothed_sets`
    is what survives the filters `_set_resolved_mappings` applies afterwards.
    Measured on the real 72B, one mapping scores 80 and 0."""
    import torch
    from compressed_tensors.utils import match_modules_set, match_named_modules
    from torch.utils._pytree import tree_leaves
    from transformers import AutoConfig, Qwen2_5_VLForConditionalGeneration
    # llmcompressor's OWN shape rule, imported and not restated, for the same
    # reason quantization_kwargs imports AWQ_IGNORE: a second copy would drift
    # from the code it predicts and the drift would be invisible. Private, so a
    # rename breaks this import -- in --dry-run, seconds in, which is where a
    # break belongs.
    from llmcompressor.modifiers.transform.awq.base import (
        _check_layers_are_compatible)
    from llmcompressor.utils.pytorch.module import get_module_to_name_dict

    config = AutoConfig.from_pretrained(str(checkpoint))
    with torch.device("meta"):
        model = Qwen2_5_VLForConditionalGeneration(config)

    quantised = quantization_kwargs()
    names = get_module_to_name_dict(model)
    # Which modules QuantizationModifier will attach a scheme to. AWQ skips a
    # set none of whose layers is being quantised, and it decides that by
    # looking for `quantization_scheme` -- an attribute nothing has this early,
    # so the membership test has to stand in for it. Same targets, same ignore
    # list, so it answers the same question one step ahead of time.
    targeted = {name for name, _ in
                match_named_modules(model, quantised["targets"],
                                    quantised["ignore"])}

    counts, smoothed = {}, {}
    for smooth, balance in AWQ_MAPPINGS:
        resolved = survives = 0
        try:
            for smooth_layers, *nested in match_modules_set(
                    model, (smooth, *balance)):
                resolved += 1
                # The same three tests _set_resolved_mappings applies, in its
                # order. It raises on the first and logs the other two.
                if len(smooth_layers) > 1:
                    raise SystemExit(
                        f"the AWQ smoothing mapping {smooth!r} matched "
                        f"{len(smooth_layers)} smooth layers in one set, and "
                        f"AWQ needs exactly one. This is the registry map's "
                        f"louder failure: an unscoped pattern collapsing every "
                        f"layer's norm into a single set.")
                balance_layers = tree_leaves(nested)
                balance_names = [names.get(layer) for layer in balance_layers]
                if not _check_layers_are_compatible(
                        smooth_layers[0], names.get(smooth_layers[0]),
                        balance_layers, balance_names):
                    continue
                if balance_layers and any(name in targeted for name in
                                          [names.get(smooth_layers[0])]
                                          + balance_names):
                    survives += 1
        except ValueError as exc:
            # match_modules_set raises when it ends holding a partial set, which
            # is the same class of fault as the wrong count and deserves the
            # same exit rather than a traceback.
            raise SystemExit(
                f"the AWQ smoothing mapping {smooth!r} cannot be resolved "
                f"against this checkpoint: {exc}") from exc
        counts[smooth], smoothed[smooth] = resolved, survives

    return {"decoder_layers": len(model.model.language_model.layers),
            "mapping_sets": counts,
            "smoothed_sets": smoothed,
            "quantised_linears": len(targeted)}


def _calibration_dataset():
    """The calibration rows, sliced before llm-compressor ever sees them.

    `get_processed_dataset` tokenises the WHOLE dataset and only then samples
    from it, so handing over all 214,670 pile-val rows spends ~15 minutes
    tokenising rows that are thrown away."""
    from datasets import load_dataset
    return load_dataset(
        CALIBRATION["dataset"],
        split=f"{CALIBRATION['split']}[:{CALIBRATION['pool']}]")


def quantise(checkpoint: Path, out: Path) -> None:
    """Load, calibrate, quantise, save. The hours-long half."""
    import torch
    from llmcompressor import oneshot
    from llmcompressor.modifiers.quantization import QuantizationModifier
    from llmcompressor.modifiers.transform.awq import AWQModifier
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

    calibration = _calibration_dataset()

    # The concrete class, not AutoModelForCausalLM: qwen2_5_vl is not in that
    # mapping at all, and llm-compressor's own loader uses it -- which is why
    # `oneshot(model="<path>")` cannot be used here. The class name is also what
    # selects the AWQ smoothing map, so loading it as anything else would take
    # the default-mappings path check_awq_mappings exists to prevent.
    #
    # No device_map: the model stays on CPU and llm-compressor's sequential
    # pipeline onloads one subgraph at a time. Handing it device_map="auto"
    # would put ~57 GiB on the card before calibration even starts, which is how
    # the autoawq route OOM'd the first time.
    processor = AutoProcessor.from_pretrained(str(checkpoint))
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        str(checkpoint), dtype=torch.bfloat16)

    # Two modifiers, in this order: AWQModifier computes and folds the smoothing
    # scales, QuantizationModifier does the 4-bit packing. This is the split
    # llmcompressor 0.13 wants -- the single AWQModifier(ignore=..., scheme=...)
    # form is a deprecated shim that returns exactly this pair.
    oneshot(model=model,
            processor=processor,
            dataset=calibration,
            recipe=[AWQModifier(mappings=awq_mappings()),
                    QuantizationModifier(**quantization_kwargs())],
            output_dir=str(out),
            text_column=CALIBRATION["text_column"],
            num_calibration_samples=CALIBRATION["samples"],
            max_seq_length=CALIBRATION["max_seq_length"],
            shuffle_calibration_samples=CALIBRATION["shuffle"],
            pad_to_max_length=CALIBRATION["pad_to_max_length"],
            sequential_targets=SEQUENTIAL_TARGETS,
            sequential_offload_device=OFFLOAD_DEVICE)
    # oneshot's own post-processing writes the model and then
    # processor.save_pretrained(output_dir), so the tokenizer, chat template and
    # image/video preprocessor configs needed to serve it land here too.
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--checkpoint", required=True, type=Path,
                    help="an EXISTING merged bf16 checkpoint, e.g. "
                         "/models/merged/zero-scale")
    ap.add_argument("--out", required=True, type=Path,
                    help="where the AWQ checkpoint is written; must be empty")
    ap.add_argument("--dry-run", action="store_true",
                    help="preflight and recipe validation only, no model load")
    args = ap.parse_args(argv)

    facts = preflight(args.checkpoint, args.out)
    print(json.dumps({**facts,
                      "calibration": CALIBRATION,
                      "sequential_targets": SEQUENTIAL_TARGETS,
                      "sequential_offload_device": OFFLOAD_DEVICE,
                      "quantization": quantization_kwargs()}, indent=1))

    # Needs the architecture but not the weights, so it belongs on the --dry-run
    # side of the load. Both counts reconcile against decoder_layers, which is
    # the acceptance bar here: an aggregate that cannot be cross-checked is a
    # number asking to be trusted.
    check_architecture(facts["architecture"])
    coverage = resolve_on_meta(args.checkpoint)
    check_mapping_coverage(coverage["mapping_sets"], coverage["decoder_layers"])
    check_smoothing_survives(coverage["smoothed_sets"],
                             coverage["decoder_layers"])
    check_quantised_scope(coverage["quantised_linears"],
                          coverage["decoder_layers"])
    print(json.dumps(coverage, indent=1))

    if args.dry_run:
        return 0

    quantise(args.checkpoint, args.out)
    written = sorted(p.name for p in args.out.glob("*.safetensors"))
    print(f"AWQ checkpoint written to {args.out}: {len(written)} shards, "
          f"{len(list(args.out.iterdir()))} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
