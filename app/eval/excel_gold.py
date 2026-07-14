"""Read a client gold Excel into {balloon_number: field dict}.

Schema is an adapter around COLUMN_ALIASES + header auto-detection: the header
row is found by scanning the first 12 rows for a 'Pos' alias. When the real
corpus arrives (Task 13), run `dump_headers` over all files; if the layout
differs, extend COLUMN_ALIASES — nothing else changes.

Numeric cells are canonicalized through normalize.canon_value at read time so
'5,5' (text) and 5.5 (float cell) ingest identically.
"""
from typing import Dict, List, Optional

from openpyxl import load_workbook

from app.eval.normalize import canon_value

# canonical field -> header aliases (matched casefolded/stripped)
COLUMN_ALIASES: Dict[str, List[str]] = {
    "pos": ["pos.", "pos", "position", "nr.", "nr", "ballon", "balloon"],
    "char_type": ["merkmal", "characteristic", "typ", "type"],
    "nominal": ["nennmaß", "nennmass", "nominal value", "nominal", "soll"],
    "upper_tol": ["o-tol", "upper-tol", "oberes abmaß", "upper tol", "otol"],
    "lower_tol": ["u-tol", "lower-tol", "unteres abmaß", "lower tol", "utol"],
    "raw": ["raw", "text", "bemerkung", "remark"],
}
_MAX_HEADER_SCAN = 12


def _norm_header(v) -> str:
    return " ".join(str(v or "").split()).casefold()


def _find_header(ws):
    """Return (header_row, {field: column}) or raise ValueError."""
    pos_aliases = set(COLUMN_ALIASES["pos"])
    for row in range(1, min(_MAX_HEADER_SCAN, ws.max_row) + 1):
        headers = {_norm_header(ws.cell(row, c).value): c
                   for c in range(1, ws.max_column + 1)}
        if not (pos_aliases & set(headers)):
            continue
        cols = {}
        for field, aliases in COLUMN_ALIASES.items():
            for a in aliases:
                if a in headers:
                    cols[field] = headers[a]
                    break
        return row, cols
    raise ValueError(f"no header row with a 'Pos' column found in {ws.title!r} "
                     f"(scanned {_MAX_HEADER_SCAN} rows)")


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return canon_value(v)
    return str(v).strip()


def read_gold_excel(path, sheet: Optional[str] = None) -> Dict[int, dict]:
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet else wb.active
    header_row, cols = _find_header(ws)
    out: Dict[int, dict] = {}
    for row in range(header_row + 1, ws.max_row + 1):
        pos_v = ws.cell(row, cols["pos"]).value
        if pos_v is None or not str(pos_v).strip():
            continue
        try:
            balloon = int(float(str(pos_v).replace(",", ".")))
        except ValueError:
            continue                      # sub-header / footer rows
        out[balloon] = {
            field: _cell_str(ws.cell(row, col).value)
            for field, col in cols.items() if field != "pos"
        }
        for field in ("char_type", "nominal", "upper_tol", "lower_tol", "raw"):
            out[balloon].setdefault(field, "")
    return out


def dump_headers(path) -> dict:
    """Day-one inspection: which header row/labels does this file use?"""
    wb = load_workbook(path, data_only=True)
    ws = wb.active
    try:
        header_row, cols = _find_header(ws)
        rows = read_gold_excel(path)
        return {
            "file": str(path),
            "sheet": ws.title,
            "header_row": header_row,
            "headers": [str(ws.cell(header_row, c).value)
                        for c in range(1, ws.max_column + 1)
                        if ws.cell(header_row, c).value is not None],
            "mapped_fields": sorted(cols),
            "n_rows": len(rows),
        }
    except ValueError as e:
        return {"file": str(path), "sheet": ws.title, "error": str(e)}
