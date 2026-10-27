"""Prediction dump I/O + the single pixel->point conversion.

A dump is one document's ExtractionResult plus the RunConfig fingerprint and
the render geometry (scale, page_rect) needed to interpret its pixel-space
boxes. Scoring never re-renders and never imports the model."""
from pathlib import Path
from typing import Tuple

from app.eval.models import PredictionDump


def to_points(box_px, scale: float, page_rect) -> Tuple[float, float, float, float]:
    """Convert an image-pixel box (rendered at `scale` px/pt) to PDF points."""
    x0, y0 = page_rect[0], page_rect[1]
    return (x0 + box_px[0] / scale, y0 + box_px[1] / scale,
            x0 + box_px[2] / scale, y0 + box_px[3] / scale)


def save_dump(dump: PredictionDump, out_dir, expect_parent=None) -> Path:
    """Atomically and owner-only (a dump holds client values): a crash never
    leaves half a dump that resume would then trust, and a link or hardlink
    at the name is replaced, never written through. predict/verify check
    and claim `out_dir` first (outputs.check_client_dir_out) and pass it as
    `expect_parent`, so a directory swapped for a link meanwhile is refused."""
    from app.eval.outputs import CLIENT_FILE_MODE, write_atomic
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{dump.doc_id}.pred.json"
    write_atomic(path, dump.model_dump_json(indent=1), mode=CLIENT_FILE_MODE,
                 expect_parent=expect_parent)
    return path


def load_dump(path) -> PredictionDump:
    return PredictionDump.model_validate_json(
        Path(path).read_text(encoding="utf-8"))
