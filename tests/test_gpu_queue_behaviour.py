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
