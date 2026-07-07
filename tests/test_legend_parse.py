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


def test_merges_consecutive_same_pos_rows():
    # The VLM sometimes emits one object per LINE of a multi-line cell; these
    # collapse back into a single row per mark number.
    raw = ('[{"pos":101,"en":"LINE ONE","de":"ZEILE EINS"},'
           '{"pos":101,"en":"LINE TWO","de":"ZEILE ZWEI"}]')
    rows = parse_rows(raw)
    assert len(rows) == 1
    assert rows[0]["pos"] == 101
    assert rows[0]["en"] == "LINE ONE LINE TWO"
    assert rows[0]["de"] == "ZEILE EINS ZEILE ZWEI"


def test_does_not_merge_sub_bullet_into_parent():
    raw = ('[{"pos":101,"en":"P","de":"P"},'
           '{"pos":101,"sub":1,"en":"C","de":"C"}]')
    rows = parse_rows(raw)
    assert len(rows) == 2


def test_does_not_merge_distinct_pos():
    raw = ('[{"pos":101,"en":"A","de":"A"},'
           '{"pos":102,"en":"B","de":"B"}]')
    assert [r["pos"] for r in parse_rows(raw)] == [101, 102]
