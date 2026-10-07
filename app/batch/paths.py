"""Where a deployment keeps its drawings (batch design §1).

    <root>/sindri.db             the index (db.py)
    <root>/docs/<id>/            input.pdf, page.png, result.json, exports/
    <root>/uploads/<id>.part     an upload still arriving (uploads.py)

Everything under it is CLIENT DATA under CLAUDE.md §1."""
import re
import unicodedata
from pathlib import Path
from typing import List, Optional

# Document ids are uuid4().hex -- the same ids the review store keys its
# sessions by. Anything else is refused before a path is built from it, so
# an id can never walk out of docs/. Upload and batch ids use the same shape.
# Anchored with \A...\Z and used with fullmatch: `$` also matches before a
# trailing newline, so "^...$" with .match accepted "<32 hex>\n". \Z keeps
# the pattern safe for a caller that still uses .match. (Any later id or
# fingerprint pattern -- e.g. a FINGERPRINT_RE -- needs the same.)
DOC_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")

_DRIVE = re.compile(r"^[A-Za-z]:")
# Control (Cc: NUL, newline, NEL...), format (Cf: bidi overrides, zero-width
# characters), line and paragraph separators (Zl, Zp). A line break forges a
# line wherever a name is written one per line; a bidi override makes a shown
# name lie about its extension; NUL truncates a name in C code.
_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})


def has_refused_chars(s: str) -> bool:
    """True if `s` carries a character no client-supplied name may hold.
    Shared by safe_rel_parts and staging.clean_batch_name: both end up as
    folder names shown to the user and written into the mass-export ZIP."""
    return any(unicodedata.category(ch) in _REFUSED_CATEGORIES for ch in s)


def safe_rel_parts(name) -> Optional[List[str]]:
    """The components of a client-supplied relative path -- a ZIP entry
    name, or a folder upload's webkitRelativePath -- or None if it is unsafe:
    absolute, drive-relative, climbing with `..`, or carrying control,
    format or line-break characters. Backslashes count as separators
    (Windows ZIP tools write them); empty and `.` components are dropped.
    The name is NFC-normalised first, so a macOS (NFD) folder and the same
    folder typed elsewhere name one folder.

    Windows strips trailing dots and spaces from a component, so `.. ` and
    `...` reach it as `..`: a component made only of dots and spaces is
    refused, and so is one that starts or ends with a space.

    Nothing is ever WRITTEN to a path built from these names -- an imported
    file always lands in docs/<new id>/input.pdf -- so this is not what keeps
    a hostile ZIP inside the data dir. It exists because the folder is shown
    to the user and becomes a folder inside the mass-export ZIP (design §4).

    NOT handled yet, and B4's export must: no length bound (on a component
    or the whole path), and NTFS-reserved names and characters (CON, NUL,
    COM1, `:` alternate streams, `<>"|?*`) pass through unchanged."""
    if not isinstance(name, str):
        return None
    s = unicodedata.normalize("NFC", name)
    if has_refused_chars(s):
        return None
    s = s.replace("\\", "/")
    if s.startswith("/") or _DRIVE.match(s):
        return None
    parts = [p for p in s.split("/") if p not in ("", ".")]
    for p in parts:
        if p.strip(" .") == "" or p != p.strip(" "):
            return None
    return parts


class DataDir:
    def __init__(self, root):
        self.root = Path(root)
        self.docs = self.root / "docs"
        self.docs.mkdir(parents=True, exist_ok=True)
        self.uploads = self.root / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.root / "sindri.db"

    def doc_dir(self, doc_id) -> Path:
        if not isinstance(doc_id, str) or not DOC_ID_RE.fullmatch(doc_id):
            raise ValueError("malformed document id")
        return self.docs / doc_id

    def part_path(self, upload_id) -> Path:
        if not isinstance(upload_id, str) or not DOC_ID_RE.fullmatch(upload_id):
            raise ValueError("malformed upload id")
        return self.uploads / f"{upload_id}.part"
