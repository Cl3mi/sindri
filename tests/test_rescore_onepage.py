"""Structural guards on rescore_onepage.sh.

It cannot run here -- `score` reaches the protected root and the guard hook
refuses, which is the whole reason the file exists for the operator to run
instead. So these pin the properties whose failure would be silent, as
tests/test_experiment_script.py does for run_experiment_gpu.sh.
"""
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "rescore_onepage.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")
# Comments in this script quote the very flags under test, so counting over the
# whole file double-counts them. Assertions about what the batch DOES must look
# at executable lines only.
CMDS = "\n".join(l for l in TEXT.splitlines() if not l.lstrip().startswith("#"))


def test_script_is_syntactically_valid():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_every_score_in_the_batch_is_filtered_to_one_sheet():
    """One unfiltered score here yields a report that silently will not compare
    against the rest of the batch -- _check_comparable raises on the doc set."""
    assert CMDS.count("--max-pages 1") == CMDS.count("runner score")


def test_the_filter_is_given_the_drawings_it_needs():
    """--max-pages without --pdfs exits 1: page counts live only in the
    drawings, and there is nowhere else to read them."""
    assert "--pdfs" in TEXT


def test_the_comparison_puts_the_control_first():
    """compare_runs reports b - a and experiment.py reads the control out of
    run_a. Reversed, the sign flips and the verdict inverts."""
    # The batch now routes every comparison through one helper, so the ordering
    # is enforced in a single place: the control is $1 and the arm is $2, and
    # the compare must pass them in that order.
    body = CMDS[CMDS.index("compare_pair()"):]
    invocation = body[body.index("runner compare"):]
    assert invocation.index("$1-scoped") < invocation.index("$2-scoped")
    # ...and every call site puts the control first.
    for line in CMDS.splitlines():
        if line.startswith("compare_pair r3-"):
            args = line.split()
            assert "control" in args[1], line


def test_no_protected_command_is_piped_or_chained():
    """CLAUDE.md §1: single, unpiped, unchained commands only."""
    for line in TEXT.splitlines():
        if "app.eval.runner" in line:
            assert "|" not in line and "&&" not in line, line


def test_the_page_count_the_writeup_is_waiting_for_is_collected():
    """§5 of the result doc is marked PENDING on exactly this number."""
    assert "runner probe" in TEXT and "--summary" in TEXT


def test_the_batch_applies_the_whole_scope_policy():
    """Policy 2026-09-09: single-sheet AND a size the renderer handles at full
    resolution. --max-pages alone leaves the four clamped drawings in, and they
    carry 283.75 review cost at 0.371 recall against 141.62 at 0.728 for the
    rest — so a batch with only half the policy reports a corpus the product
    does not claim to support."""
    assert CMDS.count("--exclude-clamped") == CMDS.count("runner score")


def test_the_requested_dpi_is_passed_with_the_clamp_filter():
    """--exclude-clamped compares each dump's effective dpi against the dpi the
    run asked for; without --dpi it would silently compare against the default
    and could keep or drop the wrong drawings."""
    assert "--dpi" in TEXT
