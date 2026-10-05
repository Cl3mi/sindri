"""Regression guards on run_merge_quant.sh, pinning a failure that was measured.

The first overnight attempt lost both cards to the same CUDA OOM ~20 minutes
into quantisation: autoawq forwards all 128 calibration samples in one pass by
default, and _compute_best_scale wanted 9.22 GiB with 6.24 GiB free while
14.75 GiB sat reserved-but-unallocated. Both 137 GB merges completed and
survived, so the retry must reuse them rather than rewrite them.

Set SINDRI_MERGE_SCRIPT to point these at another copy of the script; the suite
uses it to prove they fail against the pre-fix version.
"""
import os
from pathlib import Path

SCRIPT = Path(os.environ.get(
    "SINDRI_MERGE_SCRIPT",
    Path(__file__).parents[1] / "run_merge_quant.sh"))
TEXT = SCRIPT.read_text(encoding="utf-8")


def test_the_allocator_is_told_to_reclaim_fragmentation():
    """14.75 GiB of the card was reserved-but-unallocated when it died. Chunked
    calibration is the real fix; this recovers the fragmentation on top."""
    assert "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True" in TEXT


def test_a_failed_quantisation_can_be_retried_without_redoing_the_merge():
    """The merge writes 137 GB; quantisation is the part that failed. Without a
    resume path the retry rewrites all of it for nothing -- and preflight would
    refuse the non-empty directory and abort instead."""
    assert "--quantise-only" in TEXT
    assert "QUANTISE_ONLY" in TEXT


def test_the_resume_flag_reaches_the_script_being_run():
    """Defining the variable but never passing it would silently do a full
    rebuild while the log claims a resume."""
    invocation = TEXT[TEXT.index("python merge_lora.py"):]
    assert "$RESUME" in invocation[:400]
