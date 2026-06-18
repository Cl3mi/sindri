from pathlib import Path
from typing import Iterable
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Side, Font
from app.models import Characteristic

HEADERS = [
    ("Pos.", "Pos."),
    ("Merkmal", "Characteristic"),
    ("Nennmaß", "Nominal value"),
    ("O-TOL", "Upper-tol"),
    ("U-TOL", "Lower-tol"),
]

_thin = Side(style="thin")
_border = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
_center = Alignment(horizontal="center", vertical="center")


def write_workbook(rows: Iterable[Characteristic], path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Inspection"

    for col, (de, en) in enumerate(HEADERS, start=1):
        top = ws.cell(1, col, de)
        bot = ws.cell(2, col, en)
        for cell in (top, bot):
            cell.font = Font(bold=True)
            cell.alignment = _center
            cell.border = _border

    ordered = sorted(rows, key=lambda c: c.pos)
    for i, c in enumerate(ordered, start=3):
        values = [c.pos, c.char_type, c.nominal, c.upper_tol, c.lower_tol]
        for col, v in enumerate(values, start=1):
            cell = ws.cell(i, col, v)
            cell.alignment = _center
            cell.border = _border

    widths = [8, 18, 16, 12, 12]
    for col, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + col)].width = w

    path = Path(path)
    wb.save(path)
