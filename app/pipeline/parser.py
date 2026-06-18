import re
from app.models import Characteristic

DIAMETER = "Diameter"
RADIUS = "Radius"
FLATNESS = "Flatness"
DISTANCE = "Distance"
MATERIAL = "Material"
NOTE = "Note"

# A signed European decimal, e.g. 0,1  -0,05  12  +0,1
_NUM = r"[+\-±]?\d+(?:,\d+)?"
_NUM_RE = re.compile(_NUM)


def _clean(s: str) -> str:
    return s.replace("\n", " ").strip()


def _strip_sign(tok: str) -> str:
    return tok.lstrip("+")


def parse_value(raw: str, hint: str = "") -> Characteristic:
    text = _clean(raw)
    c = Characteristic(pos=0, raw_text=raw)

    # --- non-numeric / text-class hints first ---
    if hint == "material":
        c.char_type = MATERIAL
        c.nominal = text
        return c
    if hint == "note":
        c.char_type = NOTE
        c.nominal = text
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

    # --- symmetric tolerance: "5 ±0,1" ---
    sym = re.search(r"±\s*(\d+(?:,\d+)?)", body)
    if sym:
        nominal_part = body[:sym.start()]
        nums = _NUM_RE.findall(nominal_part)
        c.nominal = nums[0] if nums else ""
        c.upper_tol = sym.group(1)
        c.lower_tol = "-" + sym.group(1)
    else:
        nums = _NUM_RE.findall(body)
        # signed tokens (with explicit +/-) are tolerances; the rest is nominal
        signed = [n for n in nums if n[0] in "+-"]
        unsigned = [n for n in nums if n[0] not in "+-"]
        if unsigned:
            c.nominal = unsigned[0]
        elif nums:
            c.nominal = _strip_sign(nums[0])
        if len(signed) >= 1:
            c.upper_tol = _strip_sign(signed[0])
        if len(signed) >= 2:
            c.lower_tol = signed[1] if signed[1][0] == "-" else "-" + signed[1]

    # --- flatness convention: nominal is the controlled feature (0), tol is the value ---
    if c.char_type == FLATNESS and c.upper_tol == "" and c.nominal:
        c.upper_tol = c.nominal
        c.nominal = "0"

    # --- radius MAX convention: upper tol 0 when only nominal present ---
    if c.char_type == RADIUS and c.upper_tol == "" and "MAX" in upper:
        c.upper_tol = "0"

    return c
