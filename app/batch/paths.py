"""Where a deployment keeps its drawings (batch design §1).

    <root>/sindri.db             the index (db.py)
    <root>/docs/<id>/            input.pdf, page.png, result.json, exports/
    <root>/uploads/<id>.part     an upload still arriving (uploads.py)

Everything under it is CLIENT DATA under CLAUDE.md §1."""
import re
from pathlib import Path
from typing import List, Optional

# Document ids are uuid4().hex -- the same ids the review store keys its
# sessions by. Anything else is refused before a path is built from it, so
# an id can never walk out of docs/. Upload and batch ids use the same shape.
DOC_ID_RE = re.compile(r"^[0-9a-f]{32}$")

_DRIVE = re.compile(r"^[A-Za-z]:")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def safe_rel_parts(name) -> Optional[List[str]]:
    """The components of a client-supplied relative path -- a ZIP entry
    name, or a folder upload's webkitRelativePath -- or None if it is unsafe:
    absolute, drive-relative, climbing with `..`, or carrying control
    characters. Backslashes count as separators (Windows ZIP tools write
    them); empty and `.` components are dropped.

    Nothing is ever WRITTEN to a path built from these names -- an imported
    file always lands in docs/<new id>/input.pdf -- so this is not what keeps
    a hostile ZIP inside the data dir. It exists because the folder is shown
    to the user and becomes a folder inside the mass-export ZIP (design §4)."""
    if not isinstance(name, str):
        return None
    s = name.replace("\\", "/")
    if s.startswith("/") or _DRIVE.match(s) or _CONTROL.search(s):
        return None
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
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
        if not isinstance(doc_id, str) or not DOC_ID_RE.match(doc_id):
            raise ValueError("malformed document id")
        return self.docs / doc_id

    def part_path(self, upload_id) -> Path:
        if not isinstance(upload_id, str) or not DOC_ID_RE.match(upload_id):
            raise ValueError("malformed upload id")
        return self.uploads / f"{upload_id}.part"
