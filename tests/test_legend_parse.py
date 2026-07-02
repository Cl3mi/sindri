from app.pipeline.legend_parse import parse_rows


def test_parses_json_array():
    raw = '[{"pos": 101, "en": "FREE OF OIL", "de": "OELFREI"}]'
    rows = parse_rows(raw)
    assert rows == [{"pos": 101, "sub": None, "en": "FREE OF OIL",
                     "de": "OELFREI", "raw": raw}]


def test_json_multiline_cell_preserved():
    raw = '[{"pos": 101, "en": "A\\nB", "de": "C\\nD"}]'
    rows = parse_rows(raw)
    assert rows[0]["en"] == "A\nB" and rows[0]["de"] == "C\nD"


def test_falls_back_to_tab_rows():
    raw = "101\tEN\tDE"
    rows = parse_rows(raw)
    assert rows[0] == {"pos": 101, "sub": None, "en": "EN", "de": "DE",
                       "raw": "101\tEN\tDE"}


def test_falls_back_to_multispace_rows_when_no_tabs():
    raw = "101   EN TEXT   DE TEXT"
    rows = parse_rows(raw)
    assert rows[0]["pos"] == 101
    assert rows[0]["en"] == "EN TEXT"
    assert rows[0]["de"] == "DE TEXT"


def test_json_sub_bullet_carries_parent_and_index():
    raw = '[{"pos": 101, "sub": 1, "en": "x", "de": "y"}]'
    assert parse_rows(raw)[0]["sub"] == 1


def test_garbage_returns_empty():
    assert parse_rows("no rows here") == []
    assert parse_rows("") == []
