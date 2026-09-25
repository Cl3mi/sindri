"""CPU feasibility count for OCR proposals, before any GPU is spent.

For each document: render the ORIGINAL at the dump's scale, mask the CV-found
legend and title block the way extract() does (the VLM notes locator is
skipped -- so `far_from_gold` is an upper bound), run proposals.ocr_proposals
against the dump's own boxes, and count how many ISOLATED misses get a
proposal inside the match gate. Counts only.

Registered go/no-go (docs/plans/2026-09-25-review-quality-arms-plan.md,
Task 14): the GPU arm runs only if isolated_covered >= 12 on dev."""
import math
import tempfile
from pathlib import Path
from typing import Dict, List

from app.eval.dump import to_points
from app.eval.models import MatchParams


def _center(box_px, dump):
    b = to_points(box_px, dump.scale, dump.page_rect)
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def count_doc(dump, gold, score, proposals_px: List[tuple],
              params: MatchParams) -> Dict[str, int]:
    """Counting core, geometry as score_doc with reconcile_frames='none'."""
    if params.reconcile_frames != "none":
        raise ValueError("proposal_check supports reconcile_frames='none' only")
    diag = math.dist(gold.page_rect[:2], gold.page_rect[2:])
    scored = [g for g in gold.characteristics
              if getattr(g, "kind", "dimension") in params.score_kinds]
    pred_c = [_center(c.target_region, dump)
              for c in dump.result.characteristics if c.target_region]
    matched = {p.gold_balloon for p in score.pairs}
    missed = set(score.missed_balloons)
    isolated = [g for g in scored if g.balloon in missed
                and g.position_pt is not None
                and not any(math.dist(g.position_pt, pc) / diag
                            <= params.max_geo_frac for pc in pred_c)]
    # Identity gate: the same predicate score_doc uses, so the same count.
    # A real assert-equivalent that survives `python -O`.
    if len(isolated) != score.missed_isolated:
        raise AssertionError(
            f"isolated set {len(isolated)} != DocScore.missed_isolated "
            f"{score.missed_isolated} -- predicate drifted from score_doc")
    prop_c = [_center(b, dump) for b in proposals_px]

    def near(pos, pts):
        return any(math.dist(pos, p) / diag <= params.max_geo_frac
                   for p in pts)

    out = {"isolated": len(isolated),
           "isolated_covered": sum(near(g.position_pt, prop_c)
                                   for g in isolated),
           "proposals": len(prop_c), "near_unmatched_gold": 0,
           "near_matched_gold": 0, "far_from_gold": 0}
    located = [g for g in scored if g.position_pt is not None]
    for pc in prop_c:
        hits = [g for g in located
                if math.dist(pc, g.position_pt) / diag <= params.max_geo_frac]
        if not hits:
            out["far_from_gold"] += 1
        elif any(g.balloon not in matched for g in hits):
            out["near_unmatched_gold"] += 1
        else:
            out["near_matched_gold"] += 1
    return out


def proposal_check(dumps, golds, scores, pdf_dir, params) -> Dict:
    from PIL import Image
    from app.pipeline import marks_block as mb, title_block as tb
    from app.pipeline.proposals import ocr_proposals
    from app.pipeline.render import render_page
    total: Dict[str, int] = {"docs": 0, "scale_mismatch": 0}
    with tempfile.TemporaryDirectory() as tmp:
        for s in scores:
            dump, gold = dumps[s.doc_id], golds[s.doc_id]
            r = render_page(Path(pdf_dir) / f"{s.doc_id}.pdf",
                            dpi=dump.config.dpi, out_dir=Path(tmp) / s.doc_id)
            if abs(r.scale - dump.scale) > 1e-6:
                total["scale_mismatch"] += 1
                continue
            image = Image.open(r.png_path).convert("RGB")
            masked = image
            legend = mb.locate_marks_block(image)
            if legend is not None:
                masked = mb.mask_region(masked, legend)
            tbr = tb.locate_title_block(image)
            if tbr is not None:
                masked = tb.mask_region(masked, tbr)
            existing = [c.target_region for c in dump.result.characteristics
                        if c.target_region]
            c = count_doc(dump, gold, s, ocr_proposals(masked, existing),
                          params)
            total["docs"] += 1
            for k, v in c.items():
                total[k] = total.get(k, 0) + v
    # Unverified bound: every covered miss recovered and flagged (10 -> 1),
    # every far proposal kept as a false detection (+2). The VLM verifier's
    # job is to beat this by rejecting far ones; it cannot raise the first term.
    total["unverified_net_units"] = (9 * total.get("isolated_covered", 0)
                                     - 2 * total.get("far_from_gold", 0))
    return total
