"""Functional tests for run_gpu_queue.sh, run against stubbed podman/nvidia-smi.

Structural tests cannot catch control-flow bugs, and this script runs unattended
for two days -- a broken stop-on-failure or a broken resume is not something to
discover by finding 26 h of GPU wasted. So these actually execute it.
"""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "run_gpu_queue.sh"


def _stub_env(tmp_path, podman_exit=0, gpu_used="1", gpu_used_1=None):
    """A PATH where podman and nvidia-smi are shell stubs.

    podman records each invocation so the test can count stages, and exits with
    `podman_exit` so a failing stage can be simulated. nvidia-smi answers per
    CARD -- the hybrid stage claims two, and a queue that checked only the first
    would launch a 72B onto a card another job holds, which falls back to
    Tesseract and garbles every document while looking like it worked."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "podman-calls"

    (bindir / "podman").write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{calls}"\n'
        f"exit {podman_exit}\n")
    (bindir / "nvidia-smi").write_text(
        "#!/usr/bin/env bash\n"
        'idx="${!#}"\n'
        "case \"$idx\" in\n"
        f'  1) echo "{gpu_used if gpu_used_1 is None else gpu_used_1}" ;;\n'
        f'  *) echo "{gpu_used}" ;;\n'
        "esac\n")
    for f in ("podman", "nvidia-smi"):
        (bindir / f).chmod(0o755)

    env = dict(os.environ)
    env.update({
        "PATH": f"{bindir}:{env['PATH']}",
        # HOME must be the temp dir so the CDI overlay branch is not taken:
        # $HOME/cdi/nvidia.yaml does not exist here.
        "HOME": str(tmp_path),
        "RROOT": str(tmp_path / "data"),
        "LOGDIR": str(tmp_path / "logs"),
    })
    return env, calls


def _run(tmp_path, env, *stages):
    return _run_on(tmp_path, env, "0", *stages)


def _run_on(tmp_path, env, gpus, *stages):
    """The queue on a named card or cards. `gpus` is what reaches --device."""
    return subprocess.run([str(SCRIPT), gpus, *stages], env=env,
                          capture_output=True, text=True, timeout=60)


def test_a_failing_stage_stops_the_queue_rather_than_running_the_next(tmp_path):
    """The bug this test exists for: `{ ... } | tee` runs the block in a
    SUBSHELL, so FAILED+=() and break inside it cannot affect the outer loop. A
    driver with that flaw runs every later stage against missing inputs and, two
    days later, presents the result as a mysterious empty run."""
    env, calls = _stub_env(tmp_path, podman_exit=1)

    result = _run(tmp_path, env, "trainpredict", "awqgate")

    assert "STAGE FAILED" in result.stdout, result.stdout
    assert result.returncode == 1
    invocations = calls.read_text().splitlines() if calls.exists() else []
    assert len(invocations) == 1, (
        f"the queue ran {len(invocations)} stages after a failure; it must stop "
        f"at the first")
    assert "awqgate" not in "\n".join(invocations)


def test_a_completed_stage_is_skipped_on_relaunch(tmp_path):
    """The host dropped off the network for ~14 h mid-campaign. Re-launching must
    not redo 26 h of prediction that already finished."""
    env, calls = _stub_env(tmp_path)
    done = tmp_path / "data" / "runs" / "r3-trainpredict"
    done.mkdir(parents=True)
    (done / ".complete").write_text("2026-08-29T00:00:00Z")

    result = _run(tmp_path, env, "trainpredict")

    assert "SKIPPED" in result.stdout
    assert not calls.exists(), "a completed stage must not launch a container"


def test_a_successful_stage_writes_its_completion_marker(tmp_path):
    env, _ = _stub_env(tmp_path)
    _run(tmp_path, env, "trainpredict")
    assert (tmp_path / "data" / "runs" / "r3-trainpredict" / ".complete").is_file()


def test_an_occupied_card_refuses_to_start(tmp_path):
    """A 72B AWQ load into an occupied card falls back to Tesseract and garbles
    or fails every document while looking like it worked."""
    env, calls = _stub_env(tmp_path, gpu_used="65410")

    result = _run(tmp_path, env, "trainpredict")

    assert "STAGE FAILED" in result.stdout
    assert "occupied" in result.stdout
    assert not calls.exists(), "nothing may be launched onto an occupied card"


def test_an_unknown_stage_is_rejected_before_anything_runs(tmp_path):
    env, calls = _stub_env(tmp_path)
    result = _run(tmp_path, env, "trainpredict", "notastage")
    assert result.returncode == 2
    assert "unknown stage" in result.stderr
    assert not calls.exists(), "validate the whole queue before starting it"


def test_usage_is_shown_when_no_stage_is_given(tmp_path):
    env, _ = _stub_env(tmp_path)
    result = subprocess.run([str(SCRIPT), "0"], env=env, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 2
    assert "usage" in result.stderr


def test_stage_output_reaches_a_log_file_on_disk(tmp_path):
    """`podman run --rm` destroys the container's own logs on exit. The only
    timing data that survived the detectonly incident came from reading them
    before that happened -- on disk is the only place it is safe."""
    env, _ = _stub_env(tmp_path)
    _run(tmp_path, env, "trainpredict")
    log = tmp_path / "logs" / "r3-trainpredict.log"
    assert log.is_file()
    assert "stage: trainpredict" in log.read_text()


def _podman_line(calls, run_name):
    """The single podman invocation whose --out names `run_name`."""
    for line in calls.read_text().splitlines():
        if f"/data/runs/{run_name}" in line:
            return line
    raise AssertionError(f"no podman call for {run_name}:\n{calls.read_text()}")


def test_the_fresh_controls_are_known_stages(tmp_path):
    """LOW_CONF 0.6 -> 0.8 is a PIPELINE change, so every dump predicted before
    it carries review flags computed at the old threshold. Comparing a LoRA arm
    against those would credit the adapter with the threshold move -- roughly
    -3.00 of it. Both controls therefore have to be re-run on the current code,
    which means the unattended queue has to know them."""
    env, _ = _stub_env(tmp_path)
    r = _run(tmp_path, env, "awqcontrol", "nf4control")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "unknown stage" not in (r.stdout + r.stderr)


def test_the_awq_control_serves_awq_on_dev_in_the_adapter_image(tmp_path):
    """The control for lora72b-awq: same base, same image, same split, adapter
    the only difference. The image must be the peft-bearing one the arm will be
    served from -- a control built from a different image would reintroduce
    exactly the dependency risk the awqgate run was paid for."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "awqcontrol").returncode == 0
    line = _podman_line(calls, "r3-awqcontrol")
    assert "VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct-AWQ" in line, line
    assert "SINDRI_QUANT" not in line, line
    assert "--split dev" in line, line
    assert "sindri-gpu-nf4" in line, line


def test_the_nf4_control_serves_nf4_on_dev(tmp_path):
    """The control for lora72b-nf4. SINDRI_QUANT=nf4 is what makes it the NF4
    base the adapter is trained against; without it the run would serve the AWQ
    default under a name claiming otherwise, and vlm_backend records the quant
    in RunConfig.extra precisely so that cannot pass unnoticed."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "nf4control").returncode == 0
    line = _podman_line(calls, "r3-nf4control")
    assert "VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct " in line + " ", line
    assert "SINDRI_QUANT=nf4" in line, line
    assert "--split dev" in line, line


def test_a_control_never_runs_on_the_train_split(tmp_path):
    """Dev is the tuning split and the controls exist to be compared against dev
    arms. A control accidentally predicted on train would be compared against a
    different document set, which compare_runs refuses -- two days late."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "awqcontrol", "nf4control").returncode == 0
    for run_name in ("r3-awqcontrol", "r3-nf4control"):
        assert "--split train" not in _podman_line(calls, run_name)


def test_the_lora_arms_serve_the_adapter_on_their_matched_base(tmp_path):
    """The two arms Rung 3 exists to run, each a SINGLE-variable comparison:

      lora72bnf4  vs r3-nf4control   -- same NF4 base, adapter the only change
      lora72bawq  vs r3-awqcontrol   -- same AWQ base, adapter the only change

    Both controls were re-run on current code, so neither delta can be credited
    with the review-threshold move. The adapter lives at /models/adapters inside
    the sindri-models volume the queue already mounts, which is exactly where
    vlm_backend._ADAPTER_ROOT looks."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "lora72bnf4", "lora72bawq").returncode == 0

    nf4 = _podman_line(calls, "r3-lora72bnf4")
    assert "SINDRI_ADAPTER=read-lora-v1" in nf4, nf4
    assert "SINDRI_QUANT=nf4" in nf4, nf4
    assert "VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct " in nf4 + " ", nf4
    assert "--split dev" in nf4, nf4

    awq = _podman_line(calls, "r3-lora72bawq")
    assert "SINDRI_ADAPTER=read-lora-v1" in awq, awq
    assert "SINDRI_QUANT" not in awq, awq
    assert "VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct-AWQ" in awq, awq
    assert "--split dev" in awq, awq


def test_the_lora_arms_mount_the_volume_holding_the_adapter(tmp_path):
    """An arm that cannot see /models/adapters would have vlm_backend RAISE on
    the unknown adapter name -- which is the designed behaviour and far better
    than silently serving the base under a treatment arm's run name, but it is
    still nine hours lost."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "lora72bawq").returncode == 0
    assert "sindri-models:/models" in _podman_line(calls, "r3-lora72bawq")


def test_the_read_scoped_arm_is_a_stage_of_its_own(tmp_path):
    """The re-run of the LoRA arm with detection back on base weights.

    It must not reuse `lora72bnf4`'s run name. Resume compares the whole
    RunConfig and the container's git_sha is always "unknown", so a re-run under
    the old name would find 20 dumps whose config now matches on `adapter_scope`
    too only if it were also re-recorded -- and skip every document as "already
    predicted", producing the VOID arm's numbers under the new arm's name.

    Everything else is deliberately identical to lora72bnf4: same NF4 base, same
    adapter, same split, same image. The scoping in the code is the only
    variable, which is what makes n_pred == 926 a falsifiable gate rather than
    an observation."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "loraread").returncode == 0

    line = _podman_line(calls, "r3-loraread")
    assert "SINDRI_ADAPTER=read-lora-v1" in line, line
    assert "SINDRI_QUANT=nf4" in line, line
    assert "VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct " in line + " ", line
    assert "--split dev" in line, line
    assert "sindri-gpu-nf4" in line, line


def test_the_merged_awq_arm_and_its_zero_scale_control_are_stages(tmp_path):
    """Handoff §4 route 1, as two runs that differ in one thing.

    Both serve a MERGED AWQ checkpoint from the models volume, so neither sets
    SINDRI_QUANT (AWQ is a property of the checkpoint, not a runtime choice) and
    neither sets SINDRI_ADAPTER (the adapter is baked in; PEFT cannot attach to
    WQLinear_GEMM anyway, which is the whole reason this route exists).

    mergedcontrol is the zero-scale round trip. It is what says whether the
    merge and re-quantisation moved the model on their own: it must reproduce
    r3-awqcontrol at 170.05, and until it does, no delta from loramerged is
    attributable to the fine-tune."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "loramerged", "mergedcontrol").returncode == 0

    arm = _podman_line(calls, "r3-loramerged")
    assert "SINDRI_QUANT" not in arm, arm
    assert "SINDRI_ADAPTER" not in arm, arm
    assert "/models/merged/read-lora-v1-awq" in arm, arm
    assert "--split dev" in arm, arm

    ctl = _podman_line(calls, "r3-mergedcontrol")
    assert "SINDRI_QUANT" not in ctl, ctl
    assert "SINDRI_ADAPTER" not in ctl, ctl
    assert "/models/merged/zero-scale-awq" in ctl, ctl
    assert "--split dev" in ctl, ctl


def test_the_merged_stages_mount_the_volume_holding_the_checkpoints(tmp_path):
    """merge_lora.py writes into the sindri-models volume; a run that cannot see
    it would fall back to a HF model id and silently serve stock weights."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "loramerged").returncode == 0
    assert "sindri-models:/models" in _podman_line(calls, "r3-loramerged")


def test_the_vllm_arm_and_its_control_are_stages(tmp_path):
    """Route B: the adapter served at runtime on Qwen's OFFICIAL AWQ checkpoint,
    through vLLM. Nothing is quantised on this route — that is its whole
    advantage over route A, whose quantisation step has cost days.

    vllmcontrol is NOT optional and is not r3-awqcontrol. vLLM's kernels are not
    transformers' kernels, so 170.05 cannot serve as this route's baseline: the
    control prices the change of serving stack, and only then does the arm price
    the adapter. Both run the SAME official AWQ model id, so the adapter is the
    only difference between them."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "vllmcontrol", "vllmlora").returncode == 0

    ctl = _podman_line(calls, "r3-vllmcontrol")
    assert "OCR_BACKEND=vllm" in ctl, ctl
    assert "SINDRI_ADAPTER" not in ctl, ctl
    assert "SINDRI_QUANT" not in ctl, ctl
    assert "Qwen2.5-VL-72B-Instruct-AWQ" in ctl, ctl

    arm = _podman_line(calls, "r3-vllmlora")
    assert "OCR_BACKEND=vllm" in arm, arm
    assert "SINDRI_ADAPTER=read-lora-v1" in arm, arm
    assert "SINDRI_QUANT" not in arm, arm
    assert "Qwen2.5-VL-72B-Instruct-AWQ" in arm, arm


def test_the_vllm_stages_run_in_the_vllm_image_not_the_pinned_one(tmp_path):
    """The serving image is pinned at transformers 4.49.0 / autoawq 0.2.8 and
    must not move — 4.50+ breaks AWQ dispatch for Qwen2.5-VL at inference. vLLM
    brings its own stack, so it gets its own image and the pinned one is left
    exactly as every committed measurement found it."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "vllmlora").returncode == 0
    line = _podman_line(calls, "r3-vllmlora")
    assert "sindri-vllm" in line, line
    assert "sindri-gpu-nf4" not in line, line


def test_the_vllm_stages_mount_the_volume_holding_the_adapter(tmp_path):
    """vLLM loads the adapter by path from the models volume; without it the run
    would serve the base and report it under the arm's name."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "vllmlora").returncode == 0
    assert "sindri-models:/models" in _podman_line(calls, "r3-vllmlora")


def test_a_stage_that_sets_the_backend_does_not_also_get_the_default(tmp_path):
    """The queue hardcodes -e OCR_BACKEND=vlm and the vllm stages add
    -e OCR_BACKEND=vllm, so the command carries BOTH. It happens to work —
    podman takes the last -e, verified on the host — but the run then depends on
    an ordering convention rather than on what the stage asked for. If it ever
    resolved the other way the vLLM arms would silently run the transformers
    path against an AWQ checkpoint: a complete, plausible, wrong run under route
    B's name, indistinguishable except by config.extra.serving_backend."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "vllmlora").returncode == 0
    line = _podman_line(calls, "r3-vllmlora")
    assert line.count("OCR_BACKEND=") == 1, line
    assert "OCR_BACKEND=vllm" in line, line


def test_a_stage_that_does_not_set_the_backend_still_gets_the_default(tmp_path):
    """Every transformers-path stage relies on that default; removing it
    outright would fall back to Tesseract and quietly produce garbage."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "awqcontrol").returncode == 0
    line = _podman_line(calls, "r3-awqcontrol")
    assert "OCR_BACKEND=vlm" in line, line


# --- the hybrid arm: two checkpoints, two cards -----------------------------
#
# Handoff 2026-09-09 §3. The 32B localises and the 72B transcribes, which needs
# ~44 GB and ~43 GB -- 87 GB does not fit on one 80 GB H100. This is the ONE
# exception to the one-card-per-queue rule, and it is why the card argument
# takes a list.

def test_the_hybrid_stage_claims_both_cards(tmp_path):
    """Both --device flags, or the second model lands on a card the container
    cannot see and the run dies after a ~10 min load."""
    env, calls = _stub_env(tmp_path)
    r = _run_on(tmp_path, env, "0,1", "hybrid")
    assert r.returncode == 0, r.stdout + r.stderr
    line = _podman_line(calls, "r3-hybrid")
    assert "--device nvidia.com/gpu=0" in line, line
    assert "--device nvidia.com/gpu=1" in line, line


def test_the_hybrid_stage_names_both_checkpoints(tmp_path):
    """VLM_MODEL_ID is the READ model and must stay the 72B AWQ that
    r3-awqcontrol -- the control this arm is judged against -- was served with.
    VLM_DETECT_MODEL_ID is the 32B, and hybrid_backend refuses to start without
    it rather than serving one model on both passes under the arm's name."""
    env, calls = _stub_env(tmp_path)
    assert _run_on(tmp_path, env, "0,1", "hybrid").returncode == 0
    line = _podman_line(calls, "r3-hybrid")
    assert "OCR_BACKEND=hybrid" in line, line
    assert "OCR_BACKEND=vlm" not in line, line
    assert "VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct-AWQ" in line, line
    assert "VLM_DETECT_MODEL_ID=Qwen/Qwen2.5-VL-32B-Instruct-AWQ" in line, line
    assert "--split dev" in line, line


def test_the_hybrid_gate_serves_one_checkpoint_on_both_sides(tmp_path):
    """The gate prices the hybrid MACHINERY -- two processes' worth of loading,
    the device pinning, the delegation -- by serving the 72B on BOTH passes. It
    must reproduce r3-awqcontrol with every per-document delta exactly 0.0,
    exactly as awqgate priced the dependency change. Same role, same
    discipline."""
    env, calls = _stub_env(tmp_path)
    assert _run_on(tmp_path, env, "0,1", "hybridgate").returncode == 0
    line = _podman_line(calls, "r3-hybridgate")
    assert "VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct-AWQ" in line, line
    assert "VLM_DETECT_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct-AWQ" in line, line


def test_a_two_card_stage_checks_the_second_card_too(tmp_path):
    """Card 0 free, card 1 held by another job. Checking only the first would
    launch the read model onto an occupied card, and a 72B load that loses that
    race falls back to Tesseract -- which fails or garbles every document while
    the queue reports success."""
    env, calls = _stub_env(tmp_path, gpu_used="1", gpu_used_1="65410")
    r = _run_on(tmp_path, env, "0,1", "hybrid")
    assert "STAGE FAILED" in r.stdout
    assert "occupied" in r.stdout
    assert not calls.exists(), "nothing may be launched onto an occupied card"


def test_the_hybrid_refuses_to_start_on_a_single_card(tmp_path):
    """87 GB of weights on an 80 GB card OOMs partway through the first
    document, and extract._safe_read swallows read exceptions -- so it would not
    crash, it would produce a full run of empty values that scores as a
    catastrophically bad reader. Refuse before the load."""
    env, calls = _stub_env(tmp_path)
    r = _run_on(tmp_path, env, "0", "hybrid")
    assert r.returncode == 2
    assert "two cards" in r.stderr, r.stderr
    assert not calls.exists()


def test_a_single_model_stage_still_claims_exactly_one_card(tmp_path):
    """The one-card rule holds for everything else: this host has 24+ other
    users and a queue that quietly took both cards would be taking half the
    machine."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "awqcontrol").returncode == 0
    line = _podman_line(calls, "r3-awqcontrol")
    assert line.count("--device") == 1, line


# --- the test split: the only honest generalization number -----------------
#
# splits.py reserves 20% as a frozen test set and forces the structurally
# atypical `variants` into it, explicitly so cross-template generalization stays
# visible. Nothing has ever run there: every stage above is --split dev, and dev
# is the split ten-plus arms were selected against.

def test_the_test_split_stage_actually_predicts_on_test(tmp_path):
    """The whole point of the stage. Predicted on dev it would be a duplicate of
    r3-awqcontrol under a name claiming otherwise -- and the duplicate would
    LOOK right, because it would reproduce 133.93 exactly."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "awqtest").returncode == 0
    line = _podman_line(calls, "r3-awqtest")
    assert "--split test" in line, line
    assert "--split dev" not in line, line


def test_the_test_split_stage_serves_exactly_what_production_serves(tmp_path):
    """It measures the SHIPPING configuration on unseen drawings, so every
    serving variable must match r3-awqcontrol: same AWQ checkpoint, same image,
    no adapter, no quantisation override. Any difference and the number prices
    two things at once and answers neither."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "awqtest").returncode == 0
    line = _podman_line(calls, "r3-awqtest")
    assert "VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct-AWQ" in line, line
    assert "OCR_BACKEND=vlm" in line, line
    assert "SINDRI_QUANT" not in line, line
    assert "SINDRI_ADAPTER" not in line, line
    assert "sindri-gpu-nf4" in line, line
    assert line.count("--device") == 1, line


def test_no_other_stage_wanders_onto_the_test_split(tmp_path):
    """The test split is frozen and spendable once per question. A stage that
    predicted there by accident would burn it, and nothing downstream would say
    so -- the report would simply be over a different document set, which
    _check_comparable reports as an incomparability rather than as a mistake."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "awqcontrol", "nf4control", "hybrid"
                ).returncode in (0, 2)
    for line in (calls.read_text().splitlines() if calls.exists() else []):
        if "r3-awqtest" not in line:
            assert "--split test" not in line, line


# --- the crop-context arm ---------------------------------------------------

def test_the_crop_arm_changes_the_crop_and_nothing_else(tmp_path):
    """Single-variable, against r3-awqcontrol. Detection is upstream of the
    crop, so n_pred must come back at exactly 569 -- the same identity gate that
    proved loraread's scoping. Any serving difference would break that."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "cropctx").returncode == 0
    line = _podman_line(calls, "r3-cropctx")
    assert "SINDRI_CROP_PAD=24" in line, line
    assert "VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct-AWQ" in line, line
    assert "OCR_BACKEND=vlm" in line, line
    assert "SINDRI_ADAPTER" not in line, line
    assert "SINDRI_QUANT" not in line, line
    assert "--split dev" in line, line
    assert line.count("--device") == 1, line


def test_no_other_stage_carries_a_crop_knob(tmp_path):
    """Every committed measurement was taken at the default crop. A stage that
    picked one up would silently stop being comparable to the number it is
    judged against, and RunConfig.extra would be the only place it showed."""
    env, calls = _stub_env(tmp_path)
    _run(tmp_path, env, "awqcontrol", "awqtest", "hybrid")
    for line in (calls.read_text().splitlines() if calls.exists() else []):
        if "r3-cropctx" not in line:
            assert "SINDRI_CROP" not in line, line


def test_the_second_crop_dose_is_a_stage(tmp_path):
    """Two points give a direction; a third says whether the curve is monotone
    or peaked. The merge knobs were measured at two doses for exactly this
    reason, and that is what showed no intermediate setting could win."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "cropctx48").returncode == 0
    line = _podman_line(calls, "r3-cropctx48")
    assert "SINDRI_CROP_PAD=48" in line, line
    assert "VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct-AWQ" in line, line
    assert "--split dev" in line, line
    assert line.count("--device") == 1, line


# --- the policy-arms confirmation run ---------------------------------------

def test_r4control_is_current_code_with_no_knob(tmp_path):
    """r4-control confirms natively what the policy arms DERIVED from stored
    dumps (reapply-dev 118.73): current code -- Ø-zone parse plus the active
    flag/drop rules -- and nothing else. Any knob here would make the
    registered prediction (equal to the derived number to the decimal)
    untestable."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "r4control").returncode == 0
    line = _podman_line(calls, "r4-control")
    assert "VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct-AWQ" in line, line
    assert "OCR_BACKEND=vlm" in line, line
    for knob in ("SINDRI_CROP", "SINDRI_ADAPTER", "SINDRI_QUANT",
                 "SINDRI_PROPOSALS", "SINDRI_READ_PROMPT", "SINDRI_DETECT_PROMPT"):
        assert knob not in line, line
    assert "--split dev" in line, line
    assert line.count("--device") == 1, line


def test_r4controltest_is_the_shipped_config_on_the_test_split(tmp_path):
    """The native test number for what ships: current code at the shipped
    pad, on the frozen test split. The shipped policy's 149.73 there is still
    DERIVED from r3-awqtest -- and that run was predicted at pad 6, so this one
    measures the pad and the policy together, never the policy alone."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "r4controltest").returncode == 0
    line = _podman_line(calls, "r4-controltest")
    assert "--split test" in line and "--split dev" not in line, line
    assert "VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct-AWQ" in line, line
    assert "OCR_BACKEND=vlm" in line, line
    for knob in ("SINDRI_CROP", "SINDRI_ADAPTER", "SINDRI_QUANT",
                 "SINDRI_PROPOSALS", "SINDRI_READ_PROMPT", "SINDRI_DETECT_PROMPT"):
        assert knob not in line, line
    assert "sindri-gpu-nf4" in line, line
    assert line.count("--device") == 1, line


def test_only_the_two_test_stages_touch_the_test_split(tmp_path):
    env, calls = _stub_env(tmp_path)
    _run(tmp_path, env, "awqcontrol", "r4control", "cropctx", "tallpad")
    for line in (calls.read_text().splitlines() if calls.exists() else []):
        assert "--split test" not in line, line


# --- the phantom-drop confirmation runs (2026-10-06) -------------------------

_NO_KNOBS = ("SINDRI_CROP", "SINDRI_ADAPTER", "SINDRI_QUANT",
             "SINDRI_PROPOSALS", "SINDRI_READ_PROMPT", "SINDRI_DETECT_PROMPT")


def test_r5control_is_current_code_on_dev(tmp_path):
    """Native confirmation of the phantom drops, which were DERIVED from
    r4-control's dumps. Current code, no knob, dev -- so the only difference
    from r4-control is the second drop stage."""
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "r5control").returncode == 0
    line = _podman_line(calls, "r5-control")
    assert "--split dev" in line, line
    assert "VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct-AWQ" in line, line
    for knob in _NO_KNOBS:
        assert knob not in line, line
    assert line.count("--device") == 1, line


def test_r5controltest_is_current_code_on_test(tmp_path):
    env, calls = _stub_env(tmp_path)
    assert _run(tmp_path, env, "r5controltest").returncode == 0
    line = _podman_line(calls, "r5-controltest")
    assert "--split test" in line and "--split dev" not in line, line
    assert "VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct-AWQ" in line, line
    for knob in _NO_KNOBS:
        assert knob not in line, line
    assert line.count("--device") == 1, line


def test_r5control_stays_off_the_test_split(tmp_path):
    env, calls = _stub_env(tmp_path)
    _run(tmp_path, env, "r5control")
    for line in (calls.read_text().splitlines() if calls.exists() else []):
        assert "--split test" not in line, line
