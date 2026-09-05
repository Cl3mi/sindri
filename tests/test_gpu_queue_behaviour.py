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


def _stub_env(tmp_path, podman_exit=0, gpu_used="1"):
    """A PATH where podman and nvidia-smi are shell stubs.

    podman records each invocation so the test can count stages, and exits with
    `podman_exit` so a failing stage can be simulated."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "podman-calls"

    (bindir / "podman").write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{calls}"\n'
        f"exit {podman_exit}\n")
    (bindir / "nvidia-smi").write_text(
        "#!/usr/bin/env bash\n"
        f'echo "{gpu_used}"\n')
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
    return subprocess.run([str(SCRIPT), "0", *stages], env=env,
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
