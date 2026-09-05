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
# The one import from app.eval, and it is deliberate: a target must be the text
# the METRIC rewards, so the renderer has to read a char_type label exactly the
# way scoring reads it. Two vocabularies would mean training toward something
# the metric does not credit.
from app.eval.normalize import canon_char_type, char_type_equal, values_equal
from app.pipeline.parser import parse_value

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
    """This gold row cannot be expressed as text the parser maps back to it.

    Carries a short `reason` slug from a CLOSED set, because the count alone is
    not a diagnosis: the first train-split build reported 790 unrenderable rows
    and nothing about why. The slug is the only thing that may be reported --
    the label itself is the client's text, so it is deliberately kept OUT of the
    message too."""

    REASONS = ("char_type", "no_nominal", "gdt_no_hint", "gdt_no_zone",
               "gdt_hint_mismatch", "not_round_tripping")

    def __init__(self, reason: str, message: str):
        assert reason in self.REASONS, reason
        super().__init__(message)
        self.reason = reason


def _clean(v) -> str:
    return " ".join(str(v or "").split())


def _verified(text: str, gold, hint: str) -> str:
    """Return `text`, or raise if parse_value does not map it back to `gold`.

    The module's whole promise is "a target is correct exactly when parse_value
    maps it back to the row it came from". That was TESTED on eight shapes and
    never ENFORCED, so any shape nobody anticipated produced a silently wrong
    target -- worse than a dropped row, because it teaches the model to emit
    text the parser resolves to the wrong fields, which the metric then scores
    as an error. Three such shapes were found by probing, not by reasoning:

        lower-only tolerance     "5 -0,1"      -> parser reads upper='-0,1'
        two positive tolerances  "5 +0,3 0,1"  -> parser drops the lower
        negative nominal         "-3"          -> parser copies it into upper

    None is a formatting problem -- there is no text yielding an empty upper
    beside a set lower, and "5 +0,3 +0,1" parses to lower='-+0,1'. Enforcing
    the property here covers every future shape too, at one parse per row.

    The predicate is exactly score._compare_fields': char_type only when gold
    has one, the three value fields always. Stricter would drop rows the metric
    would have credited; looser would admit targets it will mark wrong."""
    back = parse_value(text, hint=hint)
    ok = (not gold.char_type
          or char_type_equal(back.char_type, gold.char_type))
    if ok:
        ok = all(values_equal(getattr(back, f), getattr(gold, f))
                 for f in ("nominal", "upper_tol", "lower_tol"))
    if not ok:
        raise UnrenderableRow(
            "not_round_tripping",
            f"row {gold.balloon}: the rendered target does not parse back to "
            f"this row under hint={hint!r}, so training on it would teach the "
            f"model text the parser resolves to different fields. Neither the "
            f"target nor the gold values are quoted here: both are client data")
    return text


def render_target(gold, hint: str = "") -> str:
    """The transcription a perfect read of `gold`'s callout would produce.

    `hint` is the parser hint the detector supplies for this callout's kind at
    inference time (`extract._HINTS`). It is part of the signature because
    parse_value's behaviour depends on it: the same text parses differently under
    hint="gdt" than under no hint, so a target is only meaningful paired with the
    hint it will be parsed under."""
    # Canonicalised through the SAME map scoring uses, never the raw label.
    # Gold labels this corpus in German and qualifies them ("Durchmesser",
    # "Diameter MIN", "Ebenheit 0,05 zu C"); _PREFIX and _GDT_SYMBOL below hold
    # only the parser's English constants. Matching the raw label against them
    # is why the first train-split build rendered 192 rows and discarded 790 --
    # 80% of every matched row on the split. The tests missed it because their
    # fixtures used the parser's vocabulary rather than gold's.
    char_type = canon_char_type(gold.char_type)
    nominal = _clean(gold.nominal)
    upper, lower = _clean(gold.upper_tol), _clean(gold.lower_tol)

    # Geometric tolerances are reachable ONLY through the hint. Measured:
    # parse_value("⏥ 0,05", hint="") returns Distance/0,05, not Flatness --
    # the parser reaches those constants nowhere else. So the GD&T rendering is
    # valid exactly when the detector called this callout gdt, and the two
    # mismatch cases below are genuinely unrenderable rather than approximable.
    if hint == "gdt":
        symbol = _GDT_SYMBOL.get(char_type)
        if symbol is None:
            raise UnrenderableRow(
                "gdt_hint_mismatch",
                f"row {gold.balloon}: detector said gdt but this char_type has "
                f"no GD&T symbol, and hint='gdt' forces the parser to a "
                f"geometric constant, so nothing can round-trip")
        if not upper:
            raise UnrenderableRow(
                "gdt_no_zone",
                f"GD&T row {gold.balloon} has no tolerance zone (upper_tol is "
                f"empty), so there is nothing to transcribe")
        return _verified(f"{symbol} {upper}", gold, hint)

    if char_type in _GDT_SYMBOL:
        raise UnrenderableRow(
            "gdt_no_hint",
            f"row {gold.balloon} is a geometric characteristic but the detector "
            f"did not call it gdt (hint={hint!r}). Emitting the symbol anyway "
            f"would put a target in the training set that the pipeline provably "
            f"cannot reproduce, which is worse than dropping the row")

    if not nominal:
        raise UnrenderableRow(
            "no_nominal",
            f"row {gold.balloon} has an empty nominal, so no transcription can "
            f"parse back to it")
    if char_type and char_type not in _PREFIX:
        raise UnrenderableRow(
            "char_type",
            f"row {gold.balloon}: char_type is not one the parser infers from a "
            f"leading symbol; rendering it would lose a scored field. The label "
            f"itself is client text and is deliberately not quoted here")

    parts = [f"{_PREFIX.get(char_type, '')}{nominal}"]
    # A Radius carrying upper_tol "0" and no lower_tol is the MAX convention:
    # parser.py sets upper_tol="0" when it sees MAX, so MAX is how it round-trips.
    if char_type == "Radius" and upper == "0" and not lower:
        parts.append("MAX")
        return _verified(" ".join(parts), gold, hint)
    if upper:
        parts.append(upper if upper.startswith(("+", "-")) else f"+{upper}")
    if lower:
        parts.append(lower if lower.startswith(("+", "-")) else lower)
    return _verified(" ".join(parts), gold, hint)
