import fitz

from app.eval.models import GoldCharacteristic
from app.eval.synthetic import make_synthetic_doc
from app.eval.ingest import build_gold_doc

RECORDS = [
    GoldCharacteristic(balloon=1, position_pt=(120.0, 90.0),
                       char_type="Diameter", nominal="20",
                       upper_tol="0,1", lower_tol="-0,1"),
    GoldCharacteristic(balloon=2, position_pt=(340.0, 200.0),
                       char_type="Distance", nominal="5,5"),
]


def test_join_recovers_positions_and_values(tmp_path):
    pdf, xlsx = make_synthetic_doc(RECORDS, tmp_path, doc_id="SYN1")
    gold = build_gold_doc(pdf, xlsx, doc_id="SYN1")
    assert gold.doc_id == "SYN1"
    assert round(gold.page_rect[2]) == 1191
    by_num = {c.balloon: c for c in gold.characteristics}
    assert set(by_num) == {1, 2}
    assert by_num[1].nominal == "20" and by_num[1].char_type == "Diameter"
    x, y = by_num[2].position_pt
    assert abs(x - 340.0) < 3 and abs(y - 200.0) < 3
    assert gold.provenance["join_rate"] == 1.0


def test_unjoined_rows_and_balloons_recorded_not_dropped_silently(tmp_path):
    pdf, xlsx = make_synthetic_doc(RECORDS, tmp_path, doc_id="SYN2")
    # Excel has a row 9 with no balloon on the page
    from openpyxl import load_workbook
    wb = load_workbook(xlsx)
    ws = wb.active
    ws.cell(4, 1, 9); ws.cell(4, 2, "Distance"); ws.cell(4, 3, "7")
    wb.save(xlsx)
    gold = build_gold_doc(pdf, xlsx, doc_id="SYN2")
    assert gold.provenance["excel_only"] == [9]
    assert gold.provenance["pdf_only"] == []
    assert gold.provenance["join_rate"] < 1.0
    assert {c.balloon for c in gold.characteristics} == {1, 2}


def test_duplicate_balloon_numbers_surfaced_in_provenance(tmp_path):
    pdf, xlsx = make_synthetic_doc(RECORDS, tmp_path, doc_id="SYN3")
    # drawings repeat balloon "1" at a second position (e.g. a second view)
    doc = fitz.open(pdf)
    page = doc[0]
    x, y = 700.0, 600.0
    page.draw_circle(fitz.Point(x, y), 9.0, color=(0, 0, 1), width=1.5)
    page.insert_text(fitz.Point(x - 5, y + 4), "1", fontsize=10, color=(0, 0, 1))
    doc.saveIncr()
    doc.close()

    gold = build_gold_doc(pdf, xlsx, doc_id="SYN3")
    assert gold.provenance["duplicate_balloons"] == [1]
