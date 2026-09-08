"""Quantising an already-merged bf16 checkpoint to AWQ, route A.

The merge half is done and must never be redone: two 137 GB bf16 checkpoints
sit on the GPU host and differ in nothing a directory listing shows. Everything
here is therefore about refusing, before the 137 GB load, a run that would
either destroy one of them or spend hours producing a checkpoint that is not
comparable to r3-awqcontrol.

Free of torch and llmcompressor imports, for the reason app.train.merge is:
those live only in the quantisation image, and this is the part with logic
worth testing. The thin half that hands these dicts to `oneshot` is exercised
on the host instead.
"""
import json

import pytest

from compress_lora import preflight


def _checkpoint(tmp_path, name="zero-scale", shards=2, architecture=None):
    """A minimal stand-in for one of the merged checkpoints: a config, an index,
    and the shards the index names."""
    out = tmp_path / name
    out.mkdir()
    (out / "config.json").write_text(json.dumps({
        "model_type": "qwen2_5_vl",
        "architectures": [architecture or "Qwen2_5_VLForConditionalGeneration"]}))
    weight_map = {f"model.layers.{i}.weight": f"model-{i + 1:05d}-of-{shards:05d}"
                  f".safetensors" for i in range(shards)}
    (out / "model.safetensors.index.json").write_text(
        json.dumps({"weight_map": weight_map}))
    for shard in set(weight_map.values()):
        (out / shard).write_text("x")
    return out


# --- refusing a run before the 137 GB load ----------------------------------


def test_a_checkpoint_that_is_not_there_is_refused(tmp_path):
    """The merge is done and this script never makes one, so a typo'd
    --checkpoint has no recovery except noticing it now: the load is the
    expensive step and it would fail with a transformers stack trace instead."""
    with pytest.raises(SystemExit, match="no merged checkpoint"):
        preflight(tmp_path / "absent", tmp_path / "out")


def test_a_directory_with_no_shards_is_refused(tmp_path):
    """Pointing at the adapter directory instead of the merge is the easy
    mistake -- /models/adapters/read-lora-v1 sits one path component away from
    /models/merged/read-lora-v1 and also holds safetensors."""
    adapter = tmp_path / "read-lora-v1"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text("{}")
    (adapter / "adapter_model.safetensors").write_text("x")

    with pytest.raises(SystemExit, match="config.json"):
        preflight(adapter, tmp_path / "out")


def test_a_shard_the_index_names_but_disk_does_not_hold_is_refused(tmp_path):
    """The reconciling check, and the reason it is worth the code: the merge
    writes 31 shards over minutes and the host has dropped an ssh channel
    mid-run before. A short checkpoint loads far enough to start quantising and
    then dies hours in -- or worse, silently quantises a model that is missing
    weights the index says it has."""
    checkpoint = _checkpoint(tmp_path, shards=3)
    (checkpoint / "model-00002-of-00003.safetensors").unlink()

    with pytest.raises(SystemExit, match="model-00002-of-00003.safetensors"):
        preflight(checkpoint, tmp_path / "out")


def test_the_facts_reconcile_the_shards_on_disk_against_the_index(tmp_path):
    """Printed rather than merely checked: the arm and the control are two runs
    of this script hours apart, and the only evidence they quantised the same
    shape of checkpoint is the two JSON headers in the two logs."""
    facts = preflight(_checkpoint(tmp_path, shards=3), tmp_path / "out")

    assert facts["shards"] == 3
    assert facts["tensors"] == 3
    assert facts["architecture"] == "Qwen2_5_VLForConditionalGeneration"


def test_an_output_directory_that_already_holds_something_is_refused(tmp_path):
    """Same rule as the merge, for the same reason: the arm and its zero-scale
    control differ in nothing a directory listing shows, so writing one over the
    other is unrecoverable -- and re-making either costs the whole merge."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "model-00001-of-00031.safetensors").write_text("x")

    with pytest.raises(SystemExit, match="not empty"):
        preflight(_checkpoint(tmp_path), out)


# --- the architecture this script is written against -------------------------

from compress_lora import ARCHITECTURE, check_architecture


def test_another_architecture_is_refused(tmp_path):
    """Everything below -- the smoothing map, the ignore list, the sequential
    target -- names Qwen2.5-VL modules. Another architecture would resolve none
    of them and llm-compressor's failure mode for an unresolved map is to smooth
    nothing and still write a checkpoint."""
    with pytest.raises(SystemExit, match="Qwen2VLForConditionalGeneration"):
        check_architecture("Qwen2VLForConditionalGeneration")


def test_the_expected_architecture_passes_quietly():
    assert check_architecture(ARCHITECTURE) is None


# --- the smoothing map, and why it is not llm-compressor's ------------------

from compress_lora import AWQ_MAPPINGS, check_mapping_coverage


def test_the_smoothing_map_is_scoped_to_the_language_model():
    """llm-compressor HAS a registry entry for this architecture and it is wrong
    here, which is why these are passed explicitly.

    Qwen2.5-VL's vision blocks use a SwiGLU MLP -- gate_proj / up_proj /
    down_proj, the same names as the decoder -- and `match_modules_set` walks
    `named_modules()` in order, so the 32 vision blocks are swept into the
    language model's sets. Measured on the real 72B config: the registry's
    post_attention_layernorm mapping collapses to ONE set holding all 80
    layernorms (llm-compressor then raises), and its up_proj -> down_proj
    mapping yields 112 sets instead of 80 -- 32 of them the vision tower, which
    AWQ_IGNORE says must not be touched. That second one does NOT raise."""
    for smooth, balance in AWQ_MAPPINGS:
        assert "language_model" in smooth
        assert all("language_model" in pattern for pattern in balance)


def test_the_four_smoothing_pairs_are_the_ones_llm_compressor_registered():
    """Scoped, not changed. The pairs themselves are AWQ's standard four and
    match llm-compressor's own Qwen2_5_VL entry; inventing different ones would
    be a second variable on top of the merge."""
    smoothed = [smooth for smooth, _ in AWQ_MAPPINGS]
    assert [s.split("*")[-1] for s in smoothed] == [
        "input_layernorm$", "v_proj$", "post_attention_layernorm$", "up_proj$"]


def test_a_map_that_resolves_to_one_set_per_decoder_layer_passes():
    """Built from AWQ_MAPPINGS rather than from two invented patterns: the
    counts the guard is handed are keyed by the smooth pattern, and a toy dict
    with fewer keys than there are mappings is the failure two tests below,
    not the passing case. Measured on the real 72B: 80 sets each, all four."""
    counts = {smooth: 80 for smooth, _ in AWQ_MAPPINGS}

    assert check_mapping_coverage(counts, 80) is None


def test_a_mapping_that_sweeps_in_extra_layers_is_refused():
    """The measured registry failure: up_proj -> down_proj resolved 112 sets on
    the 80-layer model because the 32 vision blocks matched too. Nothing raises
    on that path -- AWQ would have smoothed the tower Qwen ships unquantised,
    and the control would then be pricing that instead of the merge."""
    with pytest.raises(SystemExit, match="112"):
        check_mapping_coverage({"re:.*up_proj$": 112}, 80)


def test_a_mapping_that_resolves_to_nothing_is_refused():
    """The worse half of the same failure. `match_modules_set` returns silently
    when a target never matches, so a map that has gone stale against a new
    transformers layout smooths NOTHING and still writes a complete 4-bit
    checkpoint -- indistinguishable from a good one except in review cost."""
    with pytest.raises(SystemExit, match="up_proj\\$': 0"):
        check_mapping_coverage({"re:.*language_model.*up_proj$": 0}, 80)


def test_a_map_that_resolves_nothing_at_all_is_refused():
    """The empty dict is the same silent failure with no survivor to report it.

    `resolve_on_meta` builds its counts by iterating AWQ_MAPPINGS, so an empty
    result means the iteration produced nothing -- AWQ_MAPPINGS emptied by an
    edit, or a future resolve that filters before counting. A per-entry check
    has no entries to object to and waves it through; AWQ then runs with an
    empty mapping list, smooths nothing, and writes a checkpoint that is 4-bit,
    complete, structurally identical to a good one and worse only in review
    cost. Nothing else in the pipeline looks at this again."""
    with pytest.raises(SystemExit, match="no smoothing mapping"):
        check_mapping_coverage({}, 80)


def test_counts_that_do_not_account_for_every_mapping_are_refused():
    """The aggregate must reconcile against a count that already exists, and
    the one that exists is len(AWQ_MAPPINGS).

    `resolve_on_meta` keys its counts by the SMOOTH pattern, so two mappings
    that share a smooth layer collapse onto one dict key and the second is
    silently never checked -- and that is not hypothetical for AWQ, whose maps
    routinely smooth on `up_proj` and on `v_proj` twice over. Four mappings
    that produce three counts means one of them was never resolved at all."""
    counts = {smooth: 80 for smooth, _ in AWQ_MAPPINGS[:-1]}

    with pytest.raises(SystemExit, match="3 counts for 4 mappings"):
        check_mapping_coverage(counts, 80)


def test_a_model_with_no_decoder_layers_is_refused():
    """`layers` is the denominator of both guards, and at zero it makes both of
    them vacuous at once: every mapping resolving to 0 sets equals 0 layers, and
    `check_quantised_scope(0, 0)` agrees that 0 == 0 x 7. A run that reaches
    the recipe having smoothed nothing and quantised nothing would pass every
    structural check and produce a bf16-sized "AWQ" checkpoint.

    Reachable rather than theoretical: `resolve_on_meta` reads the layer count
    off `model.model.language_model.layers`, and a transformers that moves the
    decoder stack under another attribute is exactly the upgrade this whole
    file is defending against -- AWQ_IGNORE already carries two spellings of
    the vision tower for one such rename."""
    counts = {smooth: 0 for smooth, _ in AWQ_MAPPINGS}

    with pytest.raises(SystemExit, match="no decoder layers"):
        check_mapping_coverage(counts, 0)


# --- resolving is not smoothing ---------------------------------------------

from compress_lora import check_smoothing_survives


def test_a_mapping_dropped_for_incompatible_shapes_is_reported_not_refused():
    """Resolving a set and smoothing it are two different things, and on THIS
    model one of the four mappings does the first and not the second.

    Qwen2.5-VL-72B is GQA: 64 query heads against 8 key/value heads, so
    v_proj.out_features is 1024 while o_proj.in_features is 8192. AWQ cannot
    fold a per-channel scale across that, and llmcompressor's
    `_check_layers_are_compatible` drops the v_proj -> o_proj mapping for it --
    measured on the real config, 80 sets resolved and 0 survive. It reports
    that as one aggregate `logger.warning` among thousands of calibration
    lines, so the coverage check above says a clean 80 for a mapping that will
    smooth nothing.

    This is CORRECT and must not be refused: autoawq excludes the same pairing
    on the same grounds, so the checkpoint Qwen shipped and r3-awqcontrol's
    170.05 was measured on had it dropped too. Refusing here would refuse the
    only recipe that reproduces the reference."""
    smoothed = {"re:.*language_model.*input_layernorm$": 80,
                "re:.*language_model.*v_proj$": 0,
                "re:.*language_model.*post_attention_layernorm$": 80,
                "re:.*language_model.*up_proj$": 80}

    assert check_smoothing_survives(smoothed, 80) is None


def test_a_map_where_no_mapping_survives_is_refused():
    """The whole point of the guard, one step past resolution. Every mapping
    can resolve to exactly one set per layer and every one of them still be
    dropped -- a transformers that reshapes the MLP the way GQA reshaped
    attention would do it -- and AWQ would then quantise to 4 bits with no
    smoothing at all, which is the degradation AWQ exists to avoid. It writes
    the same 31 shards either way and nothing downstream can tell."""
    smoothed = {smooth: 0 for smooth, _ in AWQ_MAPPINGS}

    with pytest.raises(SystemExit, match="would smooth nothing"):
        check_smoothing_survives(smoothed, 80)


def test_a_mapping_that_survives_on_only_some_layers_is_refused():
    """Partial survival means the decoder is not uniform -- some layers get a
    smoothed MLP and others do not -- and the resulting checkpoint is quantised
    to two different qualities down its own depth. llmcompressor skips the
    losing layers per-layer at `logger.debug`, so the only visible trace is a
    count nobody compares against the layer total."""
    smoothed = {"re:.*language_model.*input_layernorm$": 80,
                "re:.*language_model.*v_proj$": 0,
                "re:.*language_model.*post_attention_layernorm$": 43,
                "re:.*language_model.*up_proj$": 80}

    with pytest.raises(SystemExit, match="43"):
        check_smoothing_survives(smoothed, 80)


# --- calibration: identical for the arm and the control, and not client data -

from compress_lora import CALIBRATION


def test_calibration_samples_are_not_shuffled():
    """llmcompressor's shuffle path is `RandomSampler(dataset, num_samples=...)`
    with no generator, so it draws from torch's global RNG and two processes see
    two different calibration sets. The arm and its control are two processes
    hours apart; a delta between them would then partly price the calibration
    draw. Unshuffled gives LengthAwareSampler, which is a pure function of the
    dataset."""
    assert CALIBRATION["shuffle"] is False


def test_more_rows_are_tokenised_than_are_calibrated_on():
    """LengthAwareSampler takes the LONGEST rows of what it is given, so a pool
    wider than the sample count is what fills all 512 positions -- the same
    thing autoawq got by filtering pile-val to samples over 512 tokens. Handing
    it exactly `samples` rows would calibrate on whatever short fragments came
    first."""
    assert CALIBRATION["pool"] > CALIBRATION["samples"]


def test_the_calibration_budget_is_the_one_the_autoawq_route_used():
    """128 x 512 is autoawq's default and what app.train.merge.CALIB pinned.
    Cutting either degrades the scale estimates, and the zero-scale control has
    to stand against a checkpoint Qwen calibrated properly."""
    assert CALIBRATION["samples"] == 128
    assert CALIBRATION["max_seq_length"] == 512


def test_the_calibration_corpus_is_public_not_a_local_path():
    """The NDA line. A local path here would be the client corpus reaching a
    generic-looking constant, and the two AWQ checkpoints would carry its
    values. Only a hub id is allowed."""
    assert "/models" not in CALIBRATION["dataset"]
    assert not CALIBRATION["dataset"].startswith("/")


# --- fitting a 72B into 80 GB ------------------------------------------------

from compress_lora import OFFLOAD_DEVICE, SEQUENTIAL_TARGETS


def test_one_decoder_layer_is_on_the_card_at_a_time():
    """The whole reason this route is llm-compressor and not autoawq. autoawq
    moved each block onto the card and never moved it back -- ~2.6 GiB per
    block, OOM at block 18 of 80. Naming the decoder layer partitions the model
    into 80 subgraphs, and only the live one is onloaded.

    The vision blocks are deliberately NOT named: they are in AWQ_IGNORE, so
    partitioning them would buy nothing and add subgraphs."""
    assert SEQUENTIAL_TARGETS == ["Qwen2_5_VLDecoderLayer"]


def test_everything_not_being_quantised_waits_on_the_host():
    """The 72B is 137 GB and the card is 80 GB, so the model cannot be resident.
    The host has 1007 GB of RAM; that is where it belongs."""
    assert OFFLOAD_DEVICE == "cpu"


# --- the recipe reuses what the merge already pinned -------------------------

from app.train.merge import AWQ_IGNORE, awq_scheme
from compress_lora import quantization_kwargs


def test_the_ignore_list_is_the_merge_module_s_and_not_a_second_copy():
    """Two lists would drift, and the drift would be invisible: quantising the
    vision tower makes our checkpoint structurally different from the official
    Qwen AWQ one that r3-awqcontrol's 170.05 was measured on, so the control
    would price that difference rather than the merge round trip."""
    assert quantization_kwargs()["ignore"] == list(AWQ_IGNORE)


def test_the_scheme_is_the_merge_module_s_and_not_a_second_copy():
    """Same reason: any departure from 4-bit group-128 asymmetric is a second
    variable on top of the merge and the arm stops being attributable."""
    weights = quantization_kwargs()["config_groups"]["group_0"]["weights"]
    assert weights == awq_scheme()


def test_only_linear_layers_are_targeted():
    """AWQ's mappings smooth Linear pairs; targeting anything else would ask
    llm-compressor to quantise modules the scheme was never measured on."""
    kwargs = quantization_kwargs()
    assert kwargs["targets"] == ["Linear"]
    assert kwargs["config_groups"]["group_0"]["targets"] == ["Linear"]


# --- and the ignore list must actually exclude the tower ---------------------

from compress_lora import LINEARS_PER_DECODER_LAYER, check_quantised_scope


def test_exactly_the_decoder_s_linears_are_quantised():
    """q, k, v, o, gate, up, down -- and nothing else. Measured on both configs:
    560 on the 80-layer 72B, 196 on the 28-layer 7B."""
    assert check_quantised_scope(560, 80) is None
    assert LINEARS_PER_DECODER_LAYER == 7


def test_quantising_more_than_the_decoder_is_refused():
    """AWQ_IGNORE is a list of patterns and this is the check that it still
    MATCHES. Qwen ships Qwen2.5-VL-72B-Instruct-AWQ with the vision tower
    unquantised and r3-awqcontrol's 170.05 was measured on that checkpoint, so a
    tower that slipped through would make r3-mergedcontrol price a structural
    difference instead of the merge round trip."""
    with pytest.raises(SystemExit, match="720"):
        check_quantised_scope(720, 80)


def test_quantising_less_than_the_decoder_is_refused():
    """The other direction: an ignore pattern that grew too broad would leave
    part of the model in bf16 and the checkpoint would not fit where the AWQ one
    does."""
    with pytest.raises(SystemExit, match="553"):
        check_quantised_scope(553, 80)
