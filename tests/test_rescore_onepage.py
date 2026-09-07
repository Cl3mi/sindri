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


def test_script_is_syntactically_valid():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_every_score_in_the_batch_is_filtered_to_one_sheet():
    """One unfiltered score here yields a report that silently will not compare
    against the rest of the batch -- _check_comparable raises on the doc set."""
    assert TEXT.count("--max-pages 1") == TEXT.count("runner score")


def test_the_filter_is_given_the_drawings_it_needs():
    """--max-pages without --pdfs exits 1: page counts live only in the
    drawings, and there is nowhere else to read them."""
    assert "--pdfs" in TEXT


def test_the_comparison_puts_the_control_first():
    """compare_runs reports b - a and experiment.py reads the control out of
    run_a. Reversed, the sign flips and the verdict inverts."""
    after = TEXT.index("runner compare")
    assert TEXT.index("r3-nf4control-1p", after) < TEXT.index("r3-loraread-1p",
                                                              after)


def test_no_protected_command_is_piped_or_chained():
    """CLAUDE.md §1: single, unpiped, unchained commands only."""
    for line in TEXT.splitlines():
        if "app.eval.runner" in line:
            assert "|" not in line and "&&" not in line, line


def test_the_page_count_the_writeup_is_waiting_for_is_collected():
    """§5 of the result doc is marked PENDING on exactly this number."""
    assert "runner probe" in TEXT and "--summary" in TEXT
