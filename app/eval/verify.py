"""`runner verify`: verifier verdicts for the rows today's code would ship
unflagged, written into a COPY of the stored dumps
(docs/plans/2026-10-07-verifier-registration.md §3).

It runs on the GPU host, where the drawings are and gold is not. It asks only
about deliverable rows (unflagged after all active flag and drop stages): the
verifier is a third stage, and verdicts on rows that never ship would be GPU
time spent on nothing. It writes the ORIGINAL dump plus `verifier_p`, not the
reapplied one, so scoring later still applies today's post-read code exactly
once."""
import math
from typing import Dict, Tuple

from app.eval.models import PredictionDump
from app.eval.reapply import reapply_current_code
from app.pipeline.verifier import context_crop, verifier_prompt_hash


class ScaleMismatch(ValueError):
    """The page was rendered at a different scale from the dump's boxes."""


def verify_dump(dump: PredictionDump, page, page_scale: float,
                backend) -> Tuple[PredictionDump, Dict[str, int]]:
    """(dump copy with verdicts, counts). `page` is the drawing rendered the
    way extract rendered it; `page_scale` is that render's pixels per point,
    which must equal the dump's, or every crop would be of the wrong place."""
    if not math.isclose(page_scale, dump.scale, rel_tol=1e-6):
        raise ScaleMismatch(
            f"page rendered at {page_scale:.6f} px/pt but the dump's boxes are "
            f"at {dump.scale:.6f}: crops would land on the wrong regions")
    current = reapply_current_code(dump)
    deliverable = {c.pos for c in current.result.characteristics
                   if not c.needs_review and c.target_region is not None}

    out = dump.model_copy(deep=True)
    asked = no_answer = 0
    for c in out.result.characteristics:
        if c.pos not in deliverable:
            continue
        crop, _ = context_crop(page, c.target_region)
        c.verifier_p = backend.verify_region(crop)
        asked += 1
        if c.verifier_p is None:
            no_answer += 1
    out.config.extra["verifier_prompt"] = verifier_prompt_hash()
    return out, {"rows": len(out.result.characteristics), "asked": asked,
                 "no_answer": no_answer}


def get_backend():
    # A seam for the tests; the real one loads the 72B, which only the GPU
    # host can do.
    from app.pipeline.ocr import get_backend as _get
    return _get()


def render_for_verify(pdf, dpi: int, work):
    """(page image, render scale): the SAME render extract used, so a dump's
    boxes land where they were detected. The clamp to the pixel budget is
    part of that, which is why the scale is checked rather than assumed."""
    from PIL import Image
    from app.pipeline.render import render_page
    r = render_page(pdf, dpi=dpi, out_dir=work)
    return Image.open(r.png_path).convert("RGB"), r.scale


_DUMP_SUFFIX = ".pred.json"


def cmd_verify(args) -> int:
    import json
    import sys
    from pathlib import Path
    from app.eval.dump import load_dump, save_dump
    from app.eval.runner import _PDF_GLOB, _anon, _select_docs

    src, out = Path(args.run), Path(args.out)
    dumps = {p.name[:-len(_DUMP_SUFFIX)]: p
             for p in sorted(src.glob("*" + _DUMP_SUFFIX))}
    pdfs = {p.stem: p for p in Path(args.pdfs).glob(_PDF_GLOB)}
    doc_ids, _, _ = _select_docs(dumps, args.splits, args.split)
    anon = _anon(args)
    backend = None
    verified = skipped = asked = no_answer = 0
    failures = []
    for i, doc_id in enumerate(doc_ids, 1):
        tag = f"[{i}/{len(doc_ids)}] {anon(doc_id)}"
        target = out / f"{doc_id}{_DUMP_SUFFIX}"
        if target.exists() and "verifier_prompt" in load_dump(target).config.extra:
            skipped += 1
            print(f"{tag} skipped (already verified)", file=sys.stderr)
            continue
        try:
            dump = load_dump(dumps[doc_id])
            page, scale = render_for_verify(pdfs[doc_id], dump.config.dpi,
                                            out / "_work" / doc_id)
            # Loaded lazily, after the first page renders: a missing drawing
            # should fail in seconds, not after a ten-minute model load.
            backend = backend or get_backend()
            new, stats = verify_dump(dump, page, scale, backend)
        except Exception as e:
            # The exception CLASS only: a message can carry a client path.
            failures.append({"doc": anon(doc_id), "error": type(e).__name__})
            print(f"{tag} FAILED {type(e).__name__}", file=sys.stderr)
            continue
        save_dump(new, out)
        verified += 1
        asked += stats["asked"]
        no_answer += stats["no_answer"]
        print(f"{tag} asked={stats['asked']} no_answer={stats['no_answer']}",
              file=sys.stderr)
    print(json.dumps({"n_selected": len(doc_ids), "verified": verified,
                      "skipped": skipped, "failed": len(failures),
                      "asked": asked, "no_answer": no_answer,
                      "failures": failures}, indent=1))
    return 1 if failures and not (verified or skipped) else 0
