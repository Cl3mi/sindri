"""Functional tests for run_train_lora.sh, executed against stubbed podman.

Same discipline as tests/test_gpu_queue_behaviour.py, and for the same reason:
structural greps pass on scripts whose control flow is broken. This one launches
a multi-hour training run on a shared host, and its two silent failure modes --
training on CPU because the CDI overlay was skipped, and starting on a card
another job owns -- both look like success until hours are gone.
"""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "run_train_lora.sh"


def _env(tmp_path, gpu_used="1", wait_for=""):
    """A PATH where podman and nvidia-smi are stubs that record their args."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "podman-calls"

    (bindir / "podman").write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{calls}"\n'
        "exit 0\n")
    (bindir / "nvidia-smi").write_text(
        "#!/usr/bin/env bash\n"
        f'echo "{gpu_used}"\n')
    for f in ("podman", "nvidia-smi"):
        (bindir / f).chmod(0o755)

    (tmp_path / "data" / "train" / "pairs").mkdir(parents=True)
    (tmp_path / "data" / "train" / "pairs" / "manifest.jsonl").write_text("{}\n")

    env = dict(os.environ)
    env.update({
        "PATH": f"{bindir}:{env['PATH']}",
        # HOME in the temp dir so the CDI overlay branch is not taken here:
        # $HOME/cdi/nvidia.yaml does not exist.
        "HOME": str(tmp_path),
        "RROOT": str(tmp_path / "data"),
        "LOGDIR": str(tmp_path / "logs"),
        "WAIT_FOR": wait_for,
        "WAIT_TIMEOUT": "2",
        "WAIT_POLL": "1",
    })
    return env, calls


def _run(tmp_path, env, gpu="1"):
    return subprocess.run([str(SCRIPT), gpu], env=env, capture_output=True,
                          text=True, timeout=60)


def test_it_refuses_an_occupied_card():
    """Documented way to lose a run. A 72B load into a card another job owns
    either OOMs hours in or quietly degrades, and this host has 24+ users."""


def test_an_occupied_card_stops_the_run(tmp_path):
    env, calls = _env(tmp_path, gpu_used="40000")
    r = _run(tmp_path, env)
    assert r.returncode != 0, r.stdout
    assert not calls.exists(), "podman ran on an occupied card"
    assert "occupied" in (r.stdout + r.stderr).lower()


def test_a_free_card_launches_training_with_the_manifest_and_adapter_path(tmp_path):
    env, calls = _env(tmp_path)
    r = _run(tmp_path, env)
    assert r.returncode == 0, r.stdout + r.stderr
    line = calls.read_text()
    assert "train_lora.py" in line, line
    assert "--manifest /data/train/pairs/manifest.jsonl" in line, line
    assert "/models/adapters/read-lora-v1" in line, line
    assert "--device nvidia.com/gpu=1" in line, line
    assert "sindri-train" in line, line


def test_it_waits_for_a_marker_before_taking_the_card(tmp_path):
    """The whole reason this exists as a launcher rather than a command: the
    controls hold the only card this may use, and starting before they finish
    would either be refused or -- worse -- put two 70 GB jobs on one card.

    Waiting on the COMPLETION MARKER rather than on card memory is deliberate:
    the marker is written only on success, so a failed control never silently
    hands its card to a training run whose inputs may not exist."""
    marker = tmp_path / "not-yet.complete"
    env, calls = _env(tmp_path, wait_for=str(marker))
    r = _run(tmp_path, env)
    assert r.returncode != 0, "it must give up rather than run unblocked"
    assert not calls.exists(), "podman ran before the marker appeared"
    assert "wait" in (r.stdout + r.stderr).lower()


def test_an_existing_marker_lets_it_start_immediately(tmp_path):
    marker = tmp_path / "done.complete"
    marker.write_text("2026-09-03T16:00:00Z\n")
    env, calls = _env(tmp_path, wait_for=str(marker))
    assert _run(tmp_path, env).returncode == 0
    assert "train_lora.py" in calls.read_text()


def test_hyperparameters_reach_the_container(tmp_path):
    """rank and holdout are the two settings chosen deliberately for a 735-pair
    corpus, so a launcher that silently dropped them would train a different
    experiment than the one recorded."""
    env, calls = _env(tmp_path)
    env.update({"RANK": "8", "EPOCHS": "3", "HOLDOUT_FRAC": "0.1", "SEED": "13"})
    assert _run(tmp_path, env).returncode == 0
    line = calls.read_text()
    for expected in ("--rank 8", "--epochs 3", "--holdout-frac 0.1", "--seed 13"):
        assert expected in line, (expected, line)


def test_a_finished_run_is_not_repeated(tmp_path):
    """Resumable in the same sense the queue is: relaunching after a dropped
    ssh session must not spend another ten hours redoing finished work."""
    env, calls = _env(tmp_path)
    assert _run(tmp_path, env).returncode == 0
    first = calls.read_text()
    assert _run(tmp_path, env).returncode == 0
    assert calls.read_text() == first, "training re-ran despite its marker"


def test_it_never_scores(tmp_path):
    """Gold is not on this host and must never be. The launcher may predict and
    train; scoring belongs on the operator's machine, exactly as the queue's own
    test enforces."""
    text = SCRIPT.read_text()
    for forbidden in ("runner score", "runner summary", "runner compare"):
        assert forbidden not in text, forbidden


def test_it_carries_the_cdi_overlay(tmp_path):
    """The shipped CDI spec on this host is stale. Without binding the corrected
    ~/cdi/nvidia.yaml over it the container gets NO GPU and trains on CPU --
    which looks exactly like a slow but healthy run until days are gone."""
    text = SCRIPT.read_text()
    assert "cdi/nvidia.yaml" in text
    assert "podman unshare" in text


def test_logs_land_on_disk(tmp_path):
    """`podman run --rm` destroys container logs on exit, and the loss curve is
    the only evidence of whether the adapter learned anything."""
    env, _ = _env(tmp_path)
    assert _run(tmp_path, env).returncode == 0
    logs = list((tmp_path / "logs").glob("*.log"))
    assert logs, "no log written"
    assert "train" in logs[0].read_text().lower()
