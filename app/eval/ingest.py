"""Join recovered balloons (positions) with gold Excel rows (values) into a
GoldDoc. Join failures are never silent: every unjoined balloon number lands in
provenance, and join_rate < 1.0 is the day-one signal that a document needs
manual attention (Task 13 triages those)."""
from pathlib import Path

import fitz

from app.eval.balloons import recover_balloons
from app.eval.excel_gold import read_gold_excel
from app.eval.models import GoldCharacteristic, GoldDoc


def build_gold_doc(pdf_path, excel_path, doc_id: str,
                   is_variant: bool = False, page_index: int = 0) -> GoldDoc:
    recovered = recover_balloons(pdf_path, page_index)
    nums = [b.number for b in recovered]
    duplicate_balloons = sorted({n for n in nums if nums.count(n) > 1})
    balloons = {b.number: b for b in recovered}
    rows = read_gold_excel(excel_path)

    doc = fitz.open(pdf_path)
    rect = doc[page_index].rect
    page_rect = (rect.x0, rect.y0, rect.x1, rect.y1)
    doc.close()

    joined = sorted(set(balloons) & set(rows))
    chars = [GoldCharacteristic(
                 balloon=n,
                 position_pt=balloons[n].center_pt,
                 char_type=rows[n]["char_type"],
                 nominal=rows[n]["nominal"],
                 upper_tol=rows[n]["upper_tol"],
                 lower_tol=rows[n]["lower_tol"],
                 raw=rows[n].get("raw", ""),
             ) for n in joined]
    total = len(set(balloons) | set(rows))
    return GoldDoc(
        doc_id=doc_id,
        pdf=str(Path(pdf_path)),
        excel=str(Path(excel_path)),
        page_rect=page_rect,
        characteristics=chars,
        is_variant=is_variant,
        provenance={
            "n_balloons": len(balloons),
            "n_excel_rows": len(rows),
            "pdf_only": sorted(set(balloons) - set(rows)),
            "excel_only": sorted(set(rows) - set(balloons)),
            "join_rate": (len(joined) / total) if total else 0.0,
            "duplicate_balloons": duplicate_balloons,
        },
    )
