"""Structural guards on run_merge_quant.sh.

It cannot be run end to end without a GPU host and a 137 GB base, so these pin
the properties whose failure is expensive and silent, in the same spirit as
tests/test_experiment_script.py.
"""
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "run_merge_quant.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")


def test_script_is_syntactically_valid():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_only_the_control_zeroes_the_adapter():
    """If the control did not pass --zero-scale it would BE the arm, and the two
    checkpoints differ in nothing a directory listing shows."""
    control = TEXT.index("control) NAME=")
    arm = TEXT.index("arm)     NAME=")
    assert "--zero-scale" in TEXT[control:arm]
    assert "--zero-scale" not in TEXT[arm:TEXT.index("*)", arm)]


def test_it_runs_in_the_quantisation_image_not_the_serving_one():
    """autoawq 0.2.8 in the serving image has no qwen2_5_vl wrapper and cannot
    quantise this base at all; the serving pin must not move to fix that."""
    assert "sindri-quant" in TEXT
    assert "sindri-gpu-nf4" not in TEXT


def test_the_completion_marker_is_written_only_after_a_successful_build():
    """A marker written first would make an interrupted quantisation look
    finished, and the half-written checkpoint would be served as the real one."""
    fail_exit = TEXT.index('log "FAILED')
    marker_write = TEXT.index("date -u +%FT%TZ > $MARKER")
    assert fail_exit < marker_write, "marker is written before the failure exit"


def test_an_occupied_card_is_refused_before_anything_is_built():
    """A 72B bf16 load into an occupied card OOMs partway through the merge and
    leaves a partial checkpoint behind."""
    refuse = TEXT.index("REFUSING: gpu")
    build = TEXT.index("building $NAME")
    assert refuse < build


def test_it_never_scores():
    """Gold does not exist on the GPU host, and this script has no business
    near it even if it did."""
    for forbidden in ("app.eval.runner score", "--gold", "compare"):
        assert forbidden not in TEXT
