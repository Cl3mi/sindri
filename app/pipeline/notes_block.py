"""Notes-block path: locate the general-notes table, read it as structured
bilingual data with sub-bullet linkage, mask it before the main detector
runs so its inline bullets cannot be misclassified as page-level callouts."""
import re
from typing import List, Tuple

from app.models import Note, NoteBlock


_ROW_RE = re.compile(r"^(10[0-9]|1[1-9][0-9])\t([^\t]*)\t?(.*)$")
_SUBROW_RE = re.compile(r"^(10[0-9]|1[1-9][0-9])\.(\d+)\t([^\t]*)\t?(.*)$")


def parse_notes_block(raw: str, region: Tuple[float, float, float, float]) -> NoteBlock:
    """Parse the tab-separated notes-block transcription into a NoteBlock.

    Each line is either '<pos>\\t<en>\\t<de>' or '<parent>.<sub>\\t<en>\\t<de>'.
    Malformed lines are dropped silently (non-fatal pipeline convention)."""
    notes: List[Note] = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line:
            continue
        m_sub = _SUBROW_RE.match(line)
        if m_sub:
            parent = int(m_sub.group(1))
            sub_idx = int(m_sub.group(2))
            notes.append(Note(
                pos=sub_idx, parent_pos=parent, sub_index=sub_idx,
                text_en=m_sub.group(3).strip(),
                text_de=m_sub.group(4).strip(),
                raw_text=line,
            ))
            continue
        m_top = _ROW_RE.match(line)
        if m_top:
            notes.append(Note(
                pos=int(m_top.group(1)),
                text_en=m_top.group(2).strip(),
                text_de=m_top.group(3).strip(),
                raw_text=line,
            ))
    return NoteBlock(region=region, notes=notes)


def review_flags_note(note: Note, two_columns: bool,
                      known_parents: set) -> Tuple[bool, List[str]]:
    """Return (needs_review, reasons) for a parsed note.

    Gating: an empty read is its own reason and does not also report
    'missing translation'."""
    reasons: List[str] = []
    if not (note.raw_text or "").strip():
        reasons.append("empty read")
    else:
        if two_columns and (not note.text_en.strip() or not note.text_de.strip()):
            reasons.append("missing translation")
    if note.parent_pos is not None and note.parent_pos not in known_parents:
        reasons.append("orphan sub-bullet")
    return bool(reasons), reasons
