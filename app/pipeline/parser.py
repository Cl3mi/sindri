import re
from app.models import Characteristic

DIAMETER = "Diameter"
RADIUS = "Radius"
FLATNESS = "Flatness"
DISTANCE = "Distance"
MATERIAL = "Material"
NOTE = "Note"
THEORETICAL = "Theoretical"
REFERENCE = "Reference"

# GD&T characteristic symbols -> characteristic name. Tolerant of common
# OCR/VLM substitutions for the position symbol.
_GDT_SYMBOLS = {
    "⊕": "Position",
    "⏥": FLATNESS, "▱": FLATNESS,
    "○": "Circularity", "◯": "Circularity",
    "◎": "Concentricity",
    "⌭": "Cylindricity",
    "∥": "Parallelism",
    "⊥": "Perpendicularity",
    "∠": "Angularity",
    "⌖": "Position",
}


def _gdt_type(text: str) -> str:
    for sym, name in _GDT_SYMBOLS.items():
        if sym in text:
            return name
    return FLATNESS    # default geometric tolerance when no symbol is recognized


# A signed decimal with EITHER separator, e.g. 0,1  -0.05  12  +0,1
_NUM = r"[+\-±]?\d+(?:[.,]\d+)?"
_NUM_RE = re.compile(_NUM)


def _norm(tok: str) -> str:
    """Normalize a captured number to European output: period decimal -> comma."""
    return tok.replace(".", ",")


def _clean(s: str) -> str:
    return s.replace("\n", " ").strip()


def _strip_sign(tok: str) -> str:
    return tok.lstrip("+")


def _classify(text: str):
    """Leading symbol -> (char_type, body with the class prefix stripped).

    Shared by the untoleranced `theoretical` branch and the general path so the
    two can never disagree about what a leading Ø means; the prefix is stripped
    here because leaving it in would put a stray token in front of the number
    parsing."""
    is_diameter = text.startswith("Ø") or bool(re.match(r"^[O0]\s*\d", text))
    is_radius = bool(re.match(r"^R\s*\d", text.upper()))
    if text.startswith("Ø"):
        return DIAMETER, text[1:]
    if is_diameter:
        return DIAMETER, re.sub(r"^[O0]\s*", "", text, count=1)
    if is_radius:
        return RADIUS, re.sub(r"^R\s*", "", text, count=1, flags=re.IGNORECASE)
    return DISTANCE, text


def parse_value(raw: str, hint: str = "") -> Characteristic:
    text = _clean(raw)
    c = Characteristic(pos=0, raw_text=raw)

    # --- reference / Klammermaß: a NUMERIC value in parentheses, no tolerance.
    # A parenthetical text note (no number) falls through to normal handling. ---
    if text.startswith("(") and text.endswith(")"):
        nums = _NUM_RE.findall(text)
        if nums:
            c.char_type = REFERENCE
            c.nominal = _norm(_strip_sign(nums[0]))
            return c

    # --- non-numeric / text-class hints first ---
    if hint == "material":
        c.char_type = MATERIAL
        c.nominal = text
        return c
    if hint == "note":
        c.char_type = NOTE
        c.nominal = text
        return c
    if hint == "theoretical":
        # The box means "this dimension carries no TOLERANCE" -- it is not a
        # characteristic type of its own, so only the tolerance suppression is
        # the hint's job. Emitting THEORETICAL as the char_type made every boxed
        # row unscoreable by construction: the client's inspection sheet has no
        # such value, so all 9 of them on the dev split read at field_acc 0.0000
        # and 8 shipped as SILENT errors (2026-09-17 read-accuracy-by-kind).
        # Nothing is lost by classifying the text instead -- `subtype` already
        # records that the callout was boxed.
        c.char_type, body = _classify(text)
        nums = _NUM_RE.findall(body)
        c.nominal = _norm(_strip_sign(nums[0])) if nums else ""
        return c

    if hint in ("gdt", "flatness"):
        # geometric tolerance: nominal is the controlled zero, the value is the
        # tolerance zone (spec example: Flatness -> 0 / 0,1 / 0).
        c.char_type = _gdt_type(text)
        nums = _NUM_RE.findall(text)        # ignores the leading Ø and datum letters
        c.nominal = "0"
        c.upper_tol = _norm(_strip_sign(nums[0])) if nums else ""
        c.lower_tol = "0"
        return c

    # --- classify by leading symbol ---
    upper = text.upper()
    char_type, body = _classify(text)
    if hint == "flatness":
        c.char_type = FLATNESS
    else:
        c.char_type = char_type

    # --- symmetric tolerance: "5 ±0,1" / "5 ±0.1" ---
    sym = re.search(r"±\s*(\d+(?:[.,]\d+)?)", body)
    if sym:
        nominal_part = body[:sym.start()]
        nums = _NUM_RE.findall(nominal_part)
        c.nominal = _norm(nums[0]) if nums else ""
        c.upper_tol = _norm(sym.group(1))
        c.lower_tol = "-" + _norm(sym.group(1))
    else:
        nums = _NUM_RE.findall(body)
        signed = [n for n in nums if n[0] in "+-"]
        unsigned = [n for n in nums if n[0] not in "+-"]
        if unsigned:
            c.nominal = _norm(unsigned[0])
        elif nums:
            c.nominal = _norm(_strip_sign(nums[0]))
        if len(signed) >= 1:
            c.upper_tol = _norm(_strip_sign(signed[0]))
        if len(signed) >= 2:
            c.lower_tol = _norm(signed[1]) if signed[1][0] == "-" else "-" + _norm(signed[1])
        # a single explicit upper tol followed by an unsigned 0 is a MAX-type
        # zero lower tol (e.g. "Ø6.6 +0.2 0")
        if (len(signed) == 1 and signed[0][0] == "+"
                and len(unsigned) >= 2 and _norm(unsigned[1]) in ("0", "0,0")):
            c.lower_tol = "0"

    # --- flatness convention: nominal is the controlled feature (0), tol is the value ---
    if c.char_type == FLATNESS and c.upper_tol == "" and c.nominal:
        c.upper_tol = c.nominal
        c.nominal = "0"

    # --- radius MAX convention: upper tol 0 when only nominal present ---
    if c.char_type == RADIUS and c.upper_tol == "" and "MAX" in upper:
        c.upper_tol = "0"

    return c
