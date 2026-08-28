"""Render a gold characteristic as the transcription a perfect read would emit.

This is the inverse of `app.pipeline.parser.parse_value`, and it exists because
gold gives PARSED fields (char_type, nominal, upper_tol, lower_tol) while the
read stage is trained on text. A target is correct exactly when parse_value maps
it back to the row it came from, which is a property provable on synthetic gold —
so this module is fully testable without ever touching client data. That matters:
the real targets are client values.

Two deliberate commitments, both from the design doc §3:

  * ONE tolerance form, the explicit `+0,1 -0,1`. `±0,1` round-trips only for a
    symmetric tolerance, because the parser derives the lower bound as the negated
    upper — it cannot express `+0,2 0` or a one-sided tolerance. One form
    everywhere keeps this function total over the shapes gold contains and keeps
    the target distribution consistent.
  * A row that cannot be rendered RAISES. A silent approximation would train the
    model toward a value gold does not hold, and the count of unrenderable rows
    is a finding rather than something to paper over.

Training on a canonical rendering teaches a normalisation, not a literal
transcription: a drawing printing `Ø20 ±0,1` and one printing `Ø20 +0,1 -0,1` get
the same target. That is the intent — the read stage's job is to produce text
parse_value maps to the right fields, which is exactly what the metric rewards.
"""

# char_type -> the prefix the parser needs to re-infer that char_type. The parser
# classifies by leading symbol (parser.py: is_diameter / is_radius), so the symbol
# is not decoration — dropping it loses a scored field.
_PREFIX = {"Diameter": "Ø", "Radius": "R", "Distance": "", "Theoretical": ""}

# Geometric char_types are re-inferred from their GD&T symbol under hint="gdt".
# Keys are parser.py's char_type constants; values are the symbols
# parser._GDT_SYMBOLS maps back to them.
_GDT_SYMBOL = {
    "Flatness": "⏥", "Position": "⊕", "Circularity": "○",
    "Concentricity": "◎", "Cylindricity": "⌭", "Parallelism": "∥",
    "Perpendicularity": "⊥", "Angularity": "∠",
}


class UnrenderableRow(ValueError):
    """This gold row cannot be expressed as text the parser maps back to it."""


def _clean(v) -> str:
    return " ".join(str(v or "").split())


def render_target(gold, hint: str = "") -> str:
    """The transcription a perfect read of `gold`'s callout would produce.

    `hint` is the parser hint the detector supplies for this callout's kind at
    inference time (`extract._HINTS`). It is part of the signature because
    parse_value's behaviour depends on it: the same text parses differently under
    hint="gdt" than under no hint, so a target is only meaningful paired with the
    hint it will be parsed under."""
    char_type = _clean(gold.char_type)
    nominal = _clean(gold.nominal)
    upper, lower = _clean(gold.upper_tol), _clean(gold.lower_tol)

    if hint == "gdt" or char_type in _GDT_SYMBOL:
        symbol = _GDT_SYMBOL.get(char_type)
        if symbol is None:
            raise UnrenderableRow(
                f"char_type {char_type!r} has no GD&T symbol, so the parser "
                f"cannot re-infer it under hint={hint!r}")
        if not upper:
            raise UnrenderableRow(
                f"GD&T row {gold.balloon} has no tolerance zone (upper_tol is "
                f"empty), so there is nothing to transcribe")
        return f"{symbol} {upper}"

    if not nominal:
        raise UnrenderableRow(
            f"row {gold.balloon} has an empty nominal, so no transcription can "
            f"parse back to it")
    if char_type and char_type not in _PREFIX:
        raise UnrenderableRow(
            f"char_type {char_type!r} is not one the parser infers from a "
            f"leading symbol; rendering it would lose a scored field")

    parts = [f"{_PREFIX.get(char_type, '')}{nominal}"]
    # A Radius carrying upper_tol "0" and no lower_tol is the MAX convention:
    # parser.py sets upper_tol="0" when it sees MAX, so MAX is how it round-trips.
    if char_type == "Radius" and upper == "0" and not lower:
        parts.append("MAX")
        return " ".join(parts)
    if upper:
        parts.append(upper if upper.startswith(("+", "-")) else f"+{upper}")
    if lower:
        parts.append(lower if lower.startswith(("+", "-")) else lower)
    return " ".join(parts)
