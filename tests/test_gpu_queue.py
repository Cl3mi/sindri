"""Structural guards on run_gpu_queue.sh — the HOST-side driver.

It cannot be unit-tested end to end (it needs the GPU host), so these pin the
properties that make it survive the thing it exists for: the operator's machine
going away for two days. Each one corresponds to a failure this project has
already had, or to the rule that makes a failure survivable.
"""
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "run_gpu_queue.sh"


def _text():
    return SCRIPT.read_text(encoding="utf-8")


def test_script_is_syntactically_valid():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_it_never_scores_or_pulls():
    """The gold data is NOT on the GPU host and must never be. This driver stops
    at prediction dumps; scoring, summarising and comparing all happen on the
    operator's machine after an scp. A `score` here would either fail for want of
    gold or, far worse, imply gold had been copied over."""
    text = _text()
    for forbidden in ("runner score", "runner summary", "runner compare",
                      "sync_client_data"):
        assert forbidden not in text, (
            f"{forbidden!r} belongs on the operator's machine, not the host")


def test_it_carries_the_cdi_overlay():
    """Without the podman-unshare bind of ~/cdi/nvidia.yaml the container gets no
    GPU on this host -- the shipped CDI spec is stale. run_experiment_gpu.sh
    learned this the hard way; a driver that omits it silently runs on CPU."""
    text = _text()
    assert "podman unshare" in text
    assert "cdi/nvidia.yaml" in text


def test_each_stage_is_resumable_via_a_completion_marker():
    """The host dropped off the network for ~14 h mid-campaign and killed a
    container two documents from the end. Over two unattended days another
    interruption is likely, so re-launching must skip finished stages rather than
    redo 26 h of prediction."""
    text = _text()
    assert ".complete" in text


def test_a_failing_stage_does_not_silently_continue_to_the_next():
    """A stage that failed leaves the next one running against missing inputs,
    and two days later that reads as a mysterious empty result. Record it and
    stop the queue."""
    text = _text()
    assert "STAGE FAILED" in text


def test_the_gpu_is_pinned_per_stage():
    """A 72B load into an occupied card falls back to Tesseract and silently
    ruins every document. Each stage states its own card."""
    assert "--device" in _text()


def test_logs_are_timestamped_so_per_document_timings_survive():
    """The only timing data that survived the detectonly incident came from
    `podman logs -t`, and --rm destroys that on exit. Writing timestamps to a
    file on disk is what makes a two-day unattended run analysable afterwards."""
    text = _text()
    assert "date" in text or "-t " in text


def test_it_refuses_to_start_a_stage_on_an_occupied_card():
    """The documented way to lose a run: a 72B AWQ load into a card another job
    holds falls back to Tesseract, which then fails or garbles every document
    while looking like it worked."""
    text = _text()
    assert "nvidia-smi" in text
    assert "memory.used" in text
