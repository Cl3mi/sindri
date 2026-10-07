"""What staging needs to know about a PDF before anyone decides to extract
it (batch design §2, decision 26): how many pages it has, whether the
renderer would clamp sheet 1 below the extraction dpi, and whether it can be
read at all.

`oversized` is the SAME question the harness's probe asks
(app/eval/balloons.py: effective_dpi(...) < dpi - 1.0), asked of the same
public function, app.pipeline.render.effective_dpi -- so the product's badge
and the published scope numbers cannot disagree about a drawing. app/batch
must not import app.eval, and the arithmetic is not copied: it is asked of
the renderer, which owns it."""
from dataclasses import dataclass
from typing import Optional

import fitz  # PyMuPDF

from app.pipeline.render import effective_dpi

# The dpi the worker extracts at -- app.main._extract reads it from here, so
# "oversized" cannot drift from what extraction actually asks for.
EXTRACT_DPI = 300
# The probe's tolerance: a fraction of a dpi short is not a clamp.
_CLAMP_TOLERANCE_DPI = 1.0


@dataclass(frozen=True)
class PdfFacts:
    readable: bool
    pages: Optional[int] = None
    oversized: Optional[bool] = None


def pdf_facts(path) -> PdfFacts:
    """Never raises for a bad file: anything MuPDF cannot open, a document
    that needs a user password, or one with no pages is `readable=False` --
    extraction would fail on it the same way. (A PDF encrypted with an EMPTY
    user password opens without one, extracts fine, and is readable.)"""
    try:
        with fitz.open(path) as doc:
            if doc.needs_pass or doc.page_count < 1:
                return PdfFacts(readable=False)
            rect = doc[0].rect          # render_page reads sheet 1's rect too
            dpi = effective_dpi(rect.width, rect.height, EXTRACT_DPI)
            return PdfFacts(readable=True, pages=doc.page_count,
                            oversized=dpi < EXTRACT_DPI - _CLAMP_TOLERANCE_DPI)
    except Exception:
        # Not logged here: the caller logs ids and counts, never this file.
        return PdfFacts(readable=False)
