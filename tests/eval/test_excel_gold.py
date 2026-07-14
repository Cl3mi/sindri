from openpyxl import Workbook

from app.eval.excel_gold import read_gold_excel, dump_headers


def _sheet(tmp_path, headers, rows, header_row=1, name="g.xlsx"):
    wb = Workbook()
    ws = wb.active
    for col, h in enumerate(headers, start=1):
        ws.cell(header_row, col, h)
    for i, row in enumerate(rows, start=header_row + 1):
        for col, v in enumerate(row, start=1):
            ws.cell(i, col, v)
    path = tmp_path / name
    wb.save(path)
    return path


def test_reads_house_style_sheet(tmp_path):
    path = _sheet(tmp_path, ["Pos.", "Merkmal", "Nennmaß", "O-TOL", "U-TOL"],
                  [[1, "Diameter", "20", "0,1", "-0,1"],
                   [2, "Distance", 5.5, None, None]])
    rows = read_gold_excel(path)
    assert rows[1] == {"char_type": "Diameter", "nominal": "20",
                       "upper_tol": "0,1", "lower_tol": "-0,1", "raw": ""}
    assert rows[2]["nominal"] == "5.5"       # numeric cell -> canonical string
    assert rows[2]["upper_tol"] == ""        # None -> empty


def test_header_aliases_and_offset_header_row(tmp_path):
    path = _sheet(tmp_path, ["Position", "Characteristic", "Nominal value",
                             "Upper-tol", "Lower-tol"],
                  [[7, "Radius", "2", "0", ""]], header_row=3)
    rows = read_gold_excel(path)
    assert rows[7]["char_type"] == "Radius"


def test_missing_pos_column_raises(tmp_path):
    path = _sheet(tmp_path, ["Foo", "Bar"], [[1, 2]])
    try:
        read_gold_excel(path)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Pos" in str(e)


def test_dump_headers_reports_detected_row(tmp_path):
    path = _sheet(tmp_path, ["Pos.", "Merkmal", "Nennmaß", "O-TOL", "U-TOL"],
                  [[1, "Diameter", "20", "", ""]])
    info = dump_headers(path)
    assert info["header_row"] == 1
    assert "Merkmal" in info["headers"]
    assert info["n_rows"] == 1
