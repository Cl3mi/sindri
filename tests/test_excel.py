from openpyxl import load_workbook
from app.models import Characteristic
from app.excel import write_workbook

def test_write_workbook(tmp_path):
    rows = [
        Characteristic(pos=2, char_type="Distance", nominal="3,2", upper_tol="0,05", lower_tol="-0,05"),
        Characteristic(pos=1, char_type="Distance", nominal="1,2", upper_tol="0,1", lower_tol="-0,1"),
    ]
    out = tmp_path / "out.xlsx"
    write_workbook(rows, out)
    wb = load_workbook(out)
    ws = wb.active
    assert ws.cell(1, 1).value == "Pos."
    assert ws.cell(1, 2).value == "Merkmal"
    assert ws.cell(2, 2).value == "Characteristic"
    assert ws.cell(3, 1).value == 1
    assert ws.cell(3, 3).value == "1,2"
    assert ws.cell(4, 1).value == 2
