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


def parse_value(raw: str, hint: str = "") -> Characteristic:
    text = _clean(raw)
    c = Characteristic(pos=0, raw_text=raw)

    # --- reference / Klammermaß: a value in parentheses, no tolerance ---
    stripped = text.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        nums = _NUM_RE.findall(stripped)
        c.char_type = REFERENCE
        c.nominal = _norm(_strip_sign(nums[0])) if nums else ""
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
        nums = _NUM_RE.findall(text)
        c.char_type = THEORETICAL
        c.nominal = _norm(_strip_sign(nums[0])) if nums else ""
        return c

    # --- classify by leading symbol ---
    upper = text.upper()
    is_diameter = text.startswith("Ø") or bool(re.match(r"^[O0]\s*\d", text))
    is_radius = bool(re.match(r"^R\s*\d", upper))

    # strip the class prefix so number parsing is clean
    body = text
    if text.startswith("Ø"):
        body = text[1:]
    elif is_diameter:
        body = re.sub(r"^[O0]\s*", "", text, count=1)
    elif is_radius:
        body = re.sub(r"^R\s*", "", text, count=1, flags=re.IGNORECASE)

    if hint == "flatness":
        c.char_type = FLATNESS
    elif is_diameter:
        c.char_type = DIAMETER
    elif is_radius:
        c.char_type = RADIUS
    else:
        c.char_type = DISTANCE

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
