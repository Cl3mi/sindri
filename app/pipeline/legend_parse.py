"""Shared parser for legend/notes/marks transcriptions. Accepts either a JSON
array (preferred, robust to multi-line cells) or a tolerant text form where each
row starts with a 3-digit pos followed by a tab OR 2+ spaces. Returns a list of
dicts: {"pos": int, "sub": Optional[int], "en": str, "de": str, "raw": str}."""
import json
import re

_POS_RE = re.compile(r"^(10[0-9]|1[1-9][0-9])(?:\.(\d+))?(?:\t| {2,})(.*)$")


def _from_json(raw):
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    rows = []
    for it in data:
        if not isinstance(it, dict):
            continue
        try:
            pos = int(str(it.get("pos", "")).strip())
        except (TypeError, ValueError):
            continue
        sub = it.get("sub")
        try:
            sub = int(sub) if sub is not None and str(sub) != "" else None
        except (TypeError, ValueError):
            sub = None
        rows.append({
            "pos": pos, "sub": sub,
            "en": str(it.get("en", "")).strip(),
            "de": str(it.get("de", "")).strip(),
            "raw": raw,
        })
    return rows


def _from_text(raw):
    rows = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line:
            continue
        m = _POS_RE.match(line)
        if not m:
            continue
        rest = m.group(3)
        parts = re.split(r"\t| {2,}", rest, maxsplit=1)
        en = parts[0].strip()
        de = parts[1].strip() if len(parts) > 1 else ""
        rows.append({
            "pos": int(m.group(1)),
            "sub": int(m.group(2)) if m.group(2) else None,
            "en": en, "de": de, "raw": line,
        })
    return rows


def _merge_same_pos(rows):
    """Collapse consecutive top-level rows that share a pos into one row,
    concatenating their EN/DE text. The VLM sometimes emits one object per LINE
    of a multi-line description cell; this restores one row per mark number.
    Sub-bullet rows (sub set) are never merged."""
    merged = []
    for r in rows:
        prev = merged[-1] if merged else None
        if (prev is not None and r["sub"] is None and prev["sub"] is None
                and r["pos"] == prev["pos"]):
            prev["en"] = " ".join(x for x in (prev["en"], r["en"]) if x)
            prev["de"] = " ".join(x for x in (prev["de"], r["de"]) if x)
        else:
            merged.append(dict(r))
    return merged


def parse_rows(raw: str):
    """Parse a legend transcription into row dicts. Tries JSON first (handles
    multi-line cells cleanly), then falls back to tolerant line parsing.
    Consecutive rows with the same pos are merged (see _merge_same_pos)."""
    raw = raw or ""
    rows = _from_json(raw)
    if rows is None:
        rows = _from_text(raw)
    return _merge_same_pos(rows)
