"""Scoring only the drawings the product claims to support.

Policy set 2026-09-09: the deliverable covers SINGLE-SHEET drawings at a size
the renderer handles at full resolution. Anything larger is out of scope, not a
failure to fix.

Both halves are structural, not model faults:

* multi-sheet — `render_page` takes page_index=0 and always has, so gold on
  sheet 2 is unreachable (handled by --max-pages, already built);
* oversized — `render.render_page` clamps any page over the pixel budget, and
  the four clamped documents in dev score 283.75 review cost at 0.371 recall
  against 141.62 at 0.728 for the other sixteen. Raising the budget was
  measured and lost (CLAUDE.md section 3), so these sheets need tiling that
  does not exist. Counting them scores a capability never claimed.

Reporting stays honest: the excluded count is printed and recorded, never
hidden, so "we support N of M drawings" remains answerable.
"""
import json

import pytest

from app.eval.dump import save_dump
from app.eval.models import GoldDoc
from app.eval.runner import main

from tests.eval.test_runner_e2e import _perfect_dump, _setup_corpus


def _corpus(tmp_path, clamp_synb_to=None):
    """Build the two synthetic docs; optionally make SYNB look render-clamped.

    `scale` is render pixels per PDF point, so effective dpi is scale * 72.
    A dump whose scale is below the requested dpi/72 is what a clamped page
    leaves behind."""
    pdfs, excel = _setup_corpus(tmp_path)
    gold_dir, run_dir = tmp_path / "gold", tmp_path / "runs" / "base"
    assert main(["ingest", "--pdfs", str(pdfs), "--excel", str(excel),
                 "--out", str(gold_dir)]) == 0
    for path in sorted(gold_dir.glob("*.gold.json")):
        gold = GoldDoc.model_validate_json(path.read_text())
        dump = _perfect_dump(gold.doc_id, gold)
        if clamp_synb_to and gold.doc_id == "SYNB":
            dump.scale = clamp_synb_to / 72.0
        save_dump(dump, run_dir)
    return pdfs, gold_dir, run_dir


def test_an_oversized_sheet_is_excluded_when_the_policy_asks(tmp_path):
    """A page the renderer had to clamp is out of scope. SYNB comes back at an
    effective 150 dpi against a requested 300."""
    pdfs, gold_dir, run_dir = _corpus(tmp_path, clamp_synb_to=150.0)
    out = tmp_path / "scoped.report.json"

    assert main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
                 "--dpi", "300", "--exclude-clamped",
                 "--name", "scoped", "--out", str(out)]) == 0

    report = json.loads(out.read_text())
    assert [d["doc_id"] for d in report["doc_scores"]] == ["SYNA"]


def test_a_sheet_rendered_at_full_resolution_is_kept(tmp_path):
    """The policy excludes oversized sheets, not every sheet. A document that
    rendered at the requested dpi is exactly what the product supports."""
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    out = tmp_path / "scoped.report.json"

    assert main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
                 "--dpi", "300", "--exclude-clamped",
                 "--name", "scoped", "--out", str(out)]) == 0

    assert len(json.loads(out.read_text())["doc_scores"]) == 2


def test_the_default_still_scores_everything(tmp_path):
    """Off by default. Every committed number — 173.05, 170.05, 176.40, 172.00,
    174.05 — was scored over the whole split, and a default that quietly
    narrowed the corpus would redefine all of them."""
    pdfs, gold_dir, run_dir = _corpus(tmp_path, clamp_synb_to=150.0)
    out = tmp_path / "plain.report.json"

    assert main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
                 "--name", "plain", "--out", str(out)]) == 0

    report = json.loads(out.read_text())
    assert len(report["doc_scores"]) == 2
    assert report["exclude_clamped"] is False


def test_the_policy_is_recorded_in_the_report(tmp_path):
    """A 16-document report has two very different causes — a scope policy, or
    dumps that never arrived. Without the flag on the record, "we support 16 of
    20" cannot be told from "four runs failed"."""
    pdfs, gold_dir, run_dir = _corpus(tmp_path, clamp_synb_to=150.0)
    out = tmp_path / "scoped.report.json"
    assert main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
                 "--dpi", "300", "--exclude-clamped",
                 "--name", "scoped", "--out", str(out)]) == 0

    assert json.loads(out.read_text())["exclude_clamped"] is True
