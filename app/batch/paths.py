"""Where a deployment keeps its drawings (batch design §1).

    <root>/sindri.db        the index (db.py)
    <root>/docs/<id>/       input.pdf, page.png, result.json, exports/

Everything under it is CLIENT DATA under CLAUDE.md §1."""
import re
from pathlib import Path

# Document ids are uuid4().hex -- the same ids the review store keys its
# sessions by. Anything else is refused before a path is built from it, so
# an id can never walk out of docs/.
DOC_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class DataDir:
    def __init__(self, root):
        self.root = Path(root)
        self.docs = self.root / "docs"
        self.docs.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.root / "sindri.db"

    def doc_dir(self, doc_id) -> Path:
        if not isinstance(doc_id, str) or not DOC_ID_RE.match(doc_id):
            raise ValueError("malformed document id")
        return self.docs / doc_id
