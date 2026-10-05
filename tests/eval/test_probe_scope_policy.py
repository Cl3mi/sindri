"""Answering "how many of your drawings do we support?" from the drawings alone.

The scope policy has TWO exclusions (handoff 2026-09-09 §1): multi-page sheets,
because `render_page` takes `page_index=0` and gold on sheet 2 is unreachable;
and oversized sheets, because `render.py` clamps any page over the pixel budget
and raising that budget was measured and LOST.

`probe --summary` already answered the first over the whole corpus -- 8 of 99
are multi-page. The second was only ever countable on the 20-document dev split,
because `score` needs gold. But the clamp is a pure function of page size, dpi
and the budget: no gold, no model, no GPU. So the whole corpus can be counted,
which is what the client actually asked.
"""
import math

import fitz
import pytest

from app.eval.balloons import probe_pdf
from app.eval.runner import _probe_summary
from app.pipeline.render import MAX_RENDER_PIXELS, _budget_scale

# The policy's dpi. A square page of this side lands exactly on the budget at
# 300 dpi, so a page either side of it is unambiguously in or out of scope.
_SCALE = 300 / 72.0
_BUDGET_SIDE_PT = math.sqrt(MAX_RENDER_PIXELS) / _SCALE


def _page(tmp_path, name, w_pt, h_pt, pages=1):
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=w_pt, height=h_pt)
        page.insert_text(fitz.Point(20, 40), "20 +0,1", fontsize=10)
    path = tmp_path / name
    doc.save(path)
    doc.close()
    return path


def test_an_oversized_sheet_is_flagged_and_an_ordinary_one_is_not(tmp_path):
    """The clamped drawings scored 283.75 review cost at 0.371 recall against
    141.62 at 0.728 for the rest, so whether a sheet clamps is the difference
    between a drawing the product claims and one it does not."""
    big = _page(tmp_path, "big.dwg", _BUDGET_SIDE_PT * 1.1, _BUDGET_SIDE_PT * 1.1)
    small = _page(tmp_path, "small.dwg", 595, 842)

    assert probe_pdf(big)["render_clamped"] is True
    assert probe_pdf(small)["render_clamped"] is False


def test_the_flag_comes_from_the_renderers_own_budget(tmp_path):
    """Delegated to `render._budget_scale`, never re-derived. A second copy of
    that arithmetic would keep answering plausibly on the day the budget or the
    outward pixel snap changes, and the only symptom would be a corpus count
    that quietly disagrees with what the renderer does."""
    w, h = _BUDGET_SIDE_PT * 1.3, _BUDGET_SIDE_PT * 0.8
    rec = probe_pdf(_page(tmp_path, "wide.dwg", w, h))
    expected = _budget_scale(w, h, _SCALE, MAX_RENDER_PIXELS) * 72.0
    # The record rounds to 4 decimals for digest hygiene, so the tolerance is
    # that rounding and nothing more -- a re-derived formula would miss by
    # whole dpi, not by 5e-5.
    assert rec["effective_dpi"] == pytest.approx(expected, abs=1e-3)
    assert rec["effective_dpi"] < 300.0


def test_the_summary_counts_both_exclusions_and_what_survives_them(tmp_path):
    """The number the client asked for. A drawing can fail BOTH tests, so the
    two exclusion counts do not sum to the excluded total -- `supported_docs`
    plus `excluded_docs` reconciling to `n_docs` is the identity that holds, and
    CLAUDE.md §4 requires every aggregate to reconcile against a count that
    already exists."""
    records = [
        probe_pdf(_page(tmp_path, "ok.dwg", 595, 842)),
        probe_pdf(_page(tmp_path, "multi.dwg", 595, 842, pages=3)),
        probe_pdf(_page(tmp_path, "over.dwg", _BUDGET_SIDE_PT * 1.1,
                        _BUDGET_SIDE_PT * 1.1)),
        # fails both: oversized AND multi-page
        probe_pdf(_page(tmp_path, "both.dwg", _BUDGET_SIDE_PT * 1.1,
                        _BUDGET_SIDE_PT * 1.1, pages=2)),
    ]
    s = _probe_summary(records)

    assert s["n_docs"] == 4
    assert s["multi_page_docs"] == 2
    assert s["render_clamped_docs"] == 2
    assert s["supported_docs"] == 1
    assert s["excluded_docs"] == 3
    assert s["supported_docs"] + s["excluded_docs"] == s["n_docs"]


def test_a_record_without_the_clamp_test_is_counted_as_unmeasured(tmp_path):
    """A probe record predating this field must not be counted as "fits". A
    plausible-looking 0 is exactly the defect DocScore.frame_origin_frac exists
    to prevent -- a stale report once said "all 20 frames agree" for a run where
    14 disagreed, and it cost a full analysis cycle."""
    rec = probe_pdf(_page(tmp_path, "ok.dwg", 595, 842))
    del rec["render_clamped"]

    s = _probe_summary([rec])
    assert s["render_clamped_not_measured"] == 1
    assert "supported_docs" not in s, (
        "a corpus count that cannot be computed must be absent, not guessed")
