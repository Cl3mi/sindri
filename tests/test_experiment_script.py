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
