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
    # Read the sheet FIRST: its Pos column says which balloon numbers exist on
    # this drawing, which lets recovery reject digit words that are not
    # balloons (title-block numbers, revision indices).
    _rows_for_expect = read_gold_excel(excel_path)
    # Sweep every page: 9 of the 100 drawings run to 2-4 pages, and a balloon
    # on a later sheet is still a real balloon. Only page `page_index` yields a
    # comparable POSITION though — the pipeline renders that page alone — so a
    # later-page balloon is joined by number but scored on value.
    recovered = recover_balloons(pdf_path, page_index,
                                 expect=set(_rows_for_expect), all_pages=True)
    nums = [b.number for b in recovered]
    duplicate_balloons = sorted({n for n in nums if nums.count(n) > 1})
    balloons = {b.number: b for b in recovered}
    rows = _rows_for_expect

    doc = fitz.open(pdf_path)
    rect = doc[page_index].rect
    page_rect = (rect.x0, rect.y0, rect.x1, rect.y1)
    doc.close()

    joined = sorted(set(balloons) & set(rows))
    # EVERY sheet row is gold. A characteristic whose balloon could not be
    # located still has to be findable-or-missed by the pipeline; dropping it
    # would make the eval blind to exactly the error it cares most about.
    # Rows without a position carry position_pt=None and are matched on value.
    chars = [GoldCharacteristic(
                 balloon=n,
                 position_pt=(balloons[n].center_pt
                              if n in balloons and balloons[n].page == page_index
                              else None),
                 char_type=rows[n]["char_type"],
                 nominal=rows[n]["nominal"],
                 upper_tol=rows[n]["upper_tol"],
                 lower_tol=rows[n]["lower_tol"],
                 raw=rows[n].get("raw", ""),
             ) for n in sorted(rows)]
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
            "without_position": sum(
                1 for n in rows
                if n not in balloons or balloons[n].page != page_index),
            "on_later_pages": sum(1 for n in rows if n in balloons
                                  and balloons[n].page != page_index),
            "duplicate_balloons": duplicate_balloons,
        },
    )
