"""header.json: the configuration a session was proposed under (design §2)."""
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF

from app.pipeline import policy_rules as pr
from app.pipeline import review as pipeline_review
from app.pipeline.extract import active_crop_knobs

# The REVIEW record's schema -- unrelated to app.eval's SCHEMA_VERSION.
REVIEW_SCHEMA_VERSION = 1


def build_header(pdf_path: Path, consent: bool) -> dict:
    pdf_path = Path(pdf_path)
    with fitz.open(pdf_path) as doc:
        pages = doc.page_count
    return {
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        # Container builds have no .git (CLAUDE.md §5), so the version must be
        # injected; "unknown" is honest where a guessed sha would not be.
        "app_version": os.environ.get("SINDRI_VERSION", "unknown"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "drawing_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "pages": pages,
        "consent": bool(consent),
        # Set by the audit sampler from Phase 2 on. None means NO audit design,
        # never a design with rate 0 (CLAUDE.md §4).
        "sampling": None,
        "pipeline": {
            "flag_rules": list(pr.ACTIVE_FLAG_RULES),
            "drop_stages": [list(s) for s in pr.ACTIVE_DROP_STAGES],
            "crop_knobs": active_crop_knobs(),
            "low_conf": pipeline_review.LOW_CONF,
        },
    }
