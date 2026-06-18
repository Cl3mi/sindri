from app.pipeline.notes import split_note_rows

def test_split_note_rows():
    raw = "101 CONTACT AREA PLANARITY 0,2mm\n102 PART FREE OF GREASE AND OIL"
    rows = split_note_rows(raw)
    assert rows[0].pos == 101
    assert "PLANARITY" in rows[0].nominal
    assert rows[0].char_type == "Note"
    assert rows[1].pos == 102
