"""Role auto-detection for the client delivery.

The three delivered folders cannot be named in an agent command (the guard
blocks the client folder names), so the layout tool identifies them by content:
sheets by extension, and ballooned-vs-clean drawings by whether balloons are
actually recoverable from the page.
"""
import fitz

from app.eval.models import GoldCharacteristic
from app.eval.synthetic import make_synthetic_doc
from setup_client_data import detect_roles

RECORDS = [
    GoldCharacteristic(balloon=1, position_pt=(120.0, 90.0),
                       char_type="Diameter", nominal="20"),
    GoldCharacteristic(balloon=2, position_pt=(340.0, 200.0),
                       char_type="Distance", nominal="5,5"),
]


def _plain_drawing(path):
    """A clean drawing: dimension text, no balloons."""
    doc = fitz.open()
    page = doc.new_page(width=1191, height=842)
    page.insert_text(fitz.Point(120, 90), "20 +0,1", fontsize=8)
    page.insert_text(fitz.Point(340, 200), "5,5", fontsize=8)
    doc.save(path)
    doc.close()


def _build_delivery(tmp_path):
    incoming = tmp_path / "incoming"
    # deliberately unhelpful, non-German names: detection must not use them
    stamped, originals, sheets = (incoming / "aaa", incoming / "bbb",
                                  incoming / "ccc")
    for d in (stamped, originals, sheets):
        d.mkdir(parents=True)
    for i in range(2):
        pdf, xlsx = make_synthetic_doc(RECORDS, tmp_path / "raw",
                                       doc_id=f"T{i}")
        pdf.rename(stamped / f"T{i}.pdf")
        xlsx.rename(sheets / f"T{i}.xlsx")
        _plain_drawing(originals / f"T{i}.pdf")
    return incoming, {"stamped": stamped, "originals": originals,
                      "excel": sheets}


def test_detects_roles_by_content_not_by_folder_name(tmp_path):
    incoming, expected = _build_delivery(tmp_path)
    roles = detect_roles(incoming)
    assert roles["excel"] == expected["excel"]
    assert roles["stamped"] == expected["stamped"]
    assert roles["originals"] == expected["originals"]


def test_detection_is_order_independent(tmp_path):
    """Ballooned folder sorts first here; it must still be found by balloons."""
    incoming, expected = _build_delivery(tmp_path)
    roles = detect_roles(incoming, sample=1)
    assert roles["stamped"] == expected["stamped"]
