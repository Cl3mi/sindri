"""Structural guards on run_experiment_gpu.sh. The script cannot be unit-tested
end to end (it needs a GPU host), so these pin the two properties that were
actually wrong: the gate was inlined as a heredoc, and it sat inside the
`[ -f "$CONTROL_REPORT" ]` test that made it skippable."""
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "run_experiment_gpu.sh"


def test_script_is_syntactically_valid():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_gate_is_delegated_to_the_tested_module_not_inlined():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "PYGATE" not in text, "the gate was re-inlined as a heredoc"
    assert "python3 -m app.eval.gate" in text


def test_gate_is_not_nested_inside_the_control_report_existence_test():
    """A missing baseline report must FAIL the control arm, not skip the gate."""
    text = SCRIPT.read_text(encoding="utf-8")
    guard = text.index('if [ -f "$CONTROL_REPORT" ]')
    gate = text.index("python3 -m app.eval.gate")
    between = text[guard:gate].splitlines()
    assert any(line.strip() == "fi" for line in between), (
        "no `fi` between the CONTROL_REPORT test and the gate call — "
        "the gate is still nested inside it")


def test_push_and_build_are_skippable_for_a_concurrent_second_arm():
    """Two arms run concurrently on the two cards share one checkout and one
    corpus on the GPU host. The second must not re-push or re-build under the
    first, and since both arms run the same commit it has no reason to."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "SKIP_PUSH" in text and "SKIP_BUILD" in text


def test_prompt_arms_are_registered_with_the_prompt_variant_env():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "SINDRI_READ_PROMPT" in text
    assert "SINDRI_DETECT_PROMPT" in text


def _predict_failure_block(text):
    """The text between the predict invocation's `then` and its closing `fi`."""
    start = text.index('ARM FAILED (predict)')
    # walk back to the `; then` that opens the failure branch
    open_at = text.rindex('"; then', 0, start)
    return text[open_at:text.index("\n    fi\n", start)]


def test_a_dead_ssh_is_not_reported_as_a_dead_arm():
    """detectbox's driver died at document 17 of 20 on a host at load 204. The
    container was Up 7 hours and finished the run, but the script printed
    ARM FAILED (predict). The container is started by the remote shell and
    outlives it, so predict returning non-zero does not mean the arm died --
    the host has to be asked."""
    block = _predict_failure_block(SCRIPT.read_text(encoding="utf-8"))
    assert "python3 -m app.eval.orphan" in block


def test_an_orphaned_container_stops_the_run_rather_than_freeing_the_next_arm_onto_its_card():
    """The safety-critical branch. `continue` would send the next queued arm
    onto the same pinned GPU while the orphan still holds 65 GB of it, so the
    72B AWQ load fails, get_backend falls back to Tesseract, and every document
    of that arm is worthless. The orphan branch must break."""
    block = _predict_failure_block(SCRIPT.read_text(encoding="utf-8"))
    after_orphan = block[block.index("python3 -m app.eval.orphan"):]
    assert "break" in after_orphan


def test_an_unreachable_host_is_reported_as_unknown_not_as_failed():
    """If the probe itself cannot reach the host -- the same condition that
    killed the driver -- then whether the container survived is unknown, and
    claiming either answer is a guess that costs a run."""
    block = _predict_failure_block(SCRIPT.read_text(encoding="utf-8"))
    assert "ARM UNKNOWN" in block
