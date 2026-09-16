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


def test_the_32b_is_scored_and_compared_like_every_other_arm():
    """Its +38.80 was quoted for a whole session with no comparison file behind
    it, so it had no ci95 and no weight-robustness -- and CLAUDE.md §4 is
    explicit that review cost alone has been wrong three times on this corpus.
    An arm in the table but not in the batch is an arm nobody can re-derive."""
    assert "r3-32bawq" in CMDS
    assert "compare_pair r3-awqcontrol  r3-32bawq" in CMDS


def test_the_hybrid_is_compared_against_the_read_stack_it_shares():
    """r3-awqcontrol is the same 72B AWQ checkpoint, the same transformers
    image and no adapter -- the ONLY difference is which weights localised.
    Against r3-32bawq the comparison would move two variables at once and
    credit the reader's contribution to the detector."""
    assert "compare_pair r3-awqcontrol  r3-hybrid" in CMDS


def test_the_batch_can_score_a_split_other_than_dev():
    """The test split is the only honest generalization number and nothing has
    ever scored there, because this batch hard-coded `--split dev`. It stays the
    default so every existing invocation is unchanged."""
    assert '--split "$SPLIT"' in CMDS, "the split must be a variable, not literal dev"
    assert 'SPLIT="${SPLIT:-dev}"' in CMDS, "dev must remain the default"


def test_the_comparisons_are_skipped_off_dev():
    """A test-split report against a dev report is a different document set, so
    _check_comparable refuses it -- correctly. Running them anyway would print
    NOT COMPARABLE once per pair and teach the operator to ignore that message,
    which is the one message that must never become background noise."""
    body = CMDS[CMDS.index("compare_pair()"):]
    assert 'SPLIT" = dev' in body or 'SPLIT" != dev' in body, (
        "the comparison block must be guarded on the split")


def test_the_crop_arm_is_compared_against_production():
    """r3-cropctx is a single-variable arm against r3-awqcontrol -- same
    checkpoint, same image, same prompts, only SINDRI_CROP_PAD moved -- and its
    first score had no comparison file, so it had no ci95 and no
    weight-robustness. That is the exact gap the 32B sat in for a whole session,
    and CLAUDE.md §4 is explicit that review cost alone has been wrong three
    times on this corpus."""
    assert "compare_pair r3-awqcontrol  r3-cropctx" in CMDS


def test_every_measured_arm_is_in_the_default_batch():
    """A measured run left out of the defaults gets a STALE digest: the batch
    refreshes everything else, the missing one keeps whatever fields it had when
    it was last scored, and `runner summary` then shows defaults for fields that
    report never carried. That is CLAUDE.md §4's "re-score, don't just
    re-summarise" happening by omission rather than by choice -- r3-cropctx was
    the arm it happened to, which is how its gold_coverage came back absent
    while every other arm had it."""
    defaults = next(l for l in CMDS.splitlines() if l.startswith("RUNS="))
    for run in ("r3-awqcontrol", "r3-nf4control", "r3-loraread", "r3-vllmcontrol",
                "r3-vllmlora", "r3-7bawq", "r3-32bawq", "r3-hybrid", "r3-cropctx",
                "r3-cropctx48"):
        assert run in defaults, f"{run} is measured but not in the default batch"


def test_the_second_crop_dose_is_compared_against_production():
    """Same control as the first dose -- r3-awqcontrol -- because the two doses
    are points on ONE curve. Comparing 48 against 24 would price the STEP rather
    than the setting, and the decision rule registered in
    docs/plans/2026-09-15-crop-context-arm-result.md reads both against
    production."""
    assert "compare_pair r3-awqcontrol  r3-cropctx48" in CMDS
