"""Join recovered balloons (positions) with gold Excel rows (values) into a
GoldDoc. Join failures are never silent: every unjoined balloon number lands in
provenance, and join_rate < 1.0 is the day-one signal that a document needs
manual attention (Task 13 triages those)."""
from pathlib import Path

import fitz

from app.eval.balloons import recover_balloons
from app.eval.excel_gold import read_gold_excel
from app.eval.normalize import char_type_kind
from app.eval.models import GoldCharacteristic, GoldDoc


def _kind_histogram(rows) -> dict:
    out = {}
    for row in rows.values():
        k = char_type_kind(row.get("char_type"))
        out[k] = out.get(k, 0) + 1
    return out


def _unlocated_kind_histogram(rows, balloons, page_index) -> dict:
    """Position coverage that matters: a verbal requirement never had a balloon,
    so counting it as unlocated understates how well the DIMENSIONS are covered."""
    out = {}
    for number, row in rows.items():
        if number in balloons and balloons[number].page == page_index:
            continue
        k = char_type_kind(row.get("char_type"))
        out[k] = out.get(k, 0) + 1
    return out


def _char_type_histogram(rows, balloons, page_index) -> dict:
    out = {}
    for number, row in rows.items():
        located = (number in balloons
                   and balloons[number].page == page_index)
        if located:
            continue
        label = (row.get("char_type") or "").strip() or "(blank)"
        out[label] = out.get(label, 0) + 1
    return out


def build_gold_doc(pdf_path, excel_path, doc_id: str,
                   is_variant: bool = False, page_index: int = 0,
                   use_cv: bool = False, target_pdf=None) -> GoldDoc:
    """`pdf_path` is the STAMPED drawing -- the only one carrying balloons.

    `target_pdf` is the CLEAN original the pipeline actually reads. Pass it and
    recovered positions are mapped into that sheet's coordinate space, and
    page_rect reports it. Without it, gold geometry stays in the stamped sheet's
    space, which is what produced the Rung-0 fault: on 14 of 20 dev documents the
    two sheets have different extents, so gold balloons and predictions occupied
    coordinate spaces that never overlapped and 141 real matches were scored as a
    miss plus a false detection each. Reconciling recovered recall 0.350 -> 0.646
    on the same dumps; this puts the correction at the source instead."""
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

    # Whatever the text layer could not give us, read off the rendered page.
    # Restricted to the numbers still missing, so OCR can only ever LOCATE a
    # characteristic the sheet already lists — never invent one.
    recovered_by_cv = 0
    if use_cv:
        missing = {n for n in rows
                   if n not in balloons or balloons[n].page != page_index}
        if missing:
            from app.eval.balloon_cv import detect_balloons_cv
            for found in detect_balloons_cv(pdf_path, page_index,
                                            expect=missing):
                current = balloons.get(found.number)
                if current is None or current.page != page_index:
                    balloons[found.number] = found
                    recovered_by_cv += 1

    doc = fitz.open(pdf_path)
    rect = doc[page_index].rect
    src_rect = (rect.x0, rect.y0, rect.x1, rect.y1)
    doc.close()

    # Map the stamped sheet's geometry onto the sheet the pipeline reads. Scaling
    # per axis rather than uniformly: an export can letterbox, and "scale" beat
    # "center" 0.646 vs 0.570 when the two candidate transforms were measured.
    page_rect, sx, sy = src_rect, 1.0, 1.0
    if target_pdf is not None:
        tdoc = fitz.open(target_pdf)
        trect = tdoc[page_index].rect
        page_rect = (trect.x0, trect.y0, trect.x1, trect.y1)
        tdoc.close()
        sw, sh = src_rect[2] - src_rect[0], src_rect[3] - src_rect[1]
        if sw > 0 and sh > 0:
            sx = (page_rect[2] - page_rect[0]) / sw
            sy = (page_rect[3] - page_rect[1]) / sh

    def _to_target(pos):
        if pos is None or (sx == 1.0 and sy == 1.0 and page_rect == src_rect):
            return pos
        return (page_rect[0] + (pos[0] - src_rect[0]) * sx,
                page_rect[1] + (pos[1] - src_rect[1]) * sy)

    joined = sorted(set(balloons) & set(rows))
    # EVERY sheet row is gold. A characteristic whose balloon could not be
    # located still has to be findable-or-missed by the pipeline; dropping it
    # would make the eval blind to exactly the error it cares most about.
    # Rows without a position carry position_pt=None and are matched on value.
    chars = [GoldCharacteristic(
                 balloon=n,
                 position_pt=_to_target(
                     balloons[n].center_pt
                     if n in balloons and balloons[n].page == page_index
                     else None),
                 char_type=rows[n]["char_type"],
                 nominal=rows[n]["nominal"],
                 upper_tol=rows[n]["upper_tol"],
                 lower_tol=rows[n]["lower_tol"],
                 raw=rows[n].get("raw", ""),
                 kind=char_type_kind(rows[n]["char_type"]),
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
            "recovered_by_cv": recovered_by_cv,
            # Are unlocated rows a detection failure, or characteristics that
            # were never ballooned (material, general notes)? char_type is a
            # shared category label, so this is safe to aggregate.
            "kinds": _kind_histogram(rows),
            "unlocated_kinds": _unlocated_kind_histogram(
                rows, balloons, page_index),
            "unlocated_char_types": _char_type_histogram(
                rows, balloons, page_index),
            "duplicate_balloons": duplicate_balloons,
            # Which sheet the balloons were measured on and what was applied to
            # bring them into the pipeline's space. In provenance, which
            # gold_hash excludes, so recording the transform cannot itself
            # invalidate a corpus. [1.0, 1.0] means no remapping was needed.
            "source_page_rect": list(src_rect),
            "target_scale": [round(sx, 9), round(sy, 9)],
        },
    )
