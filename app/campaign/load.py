"""Load a campaign bundle into a running instance (campaign design §2).

    docker compose exec sindri python -m app.campaign.load /import/pilot-1

Runs beside the server on the same data dir: it only writes files and index
rows and queues them; the server's own worker turns each into a review
record, skipping extraction because result.json is already there (plan C1
decision 2). Every entry is validated BEFORE anything is written, so a bad
bundle leaves the instance untouched. Idempotent by drawing bytes.

Every message this prints names an entry (a salted doc hash) and an error
TYPE, never an exception's text: a pydantic, PyMuPDF or OS message can quote
a client value or a path (CLAUDE.md §1)."""
import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import fitz
from pydantic import ValidationError

from app.batch import db
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.models import Characteristic

_FILES = ("input.pdf", "page.png", "result.json", "campaign.json")


class BundleError(ValueError):
    pass


def _check(entry: Path) -> dict:
    """The entry's campaign facts plus its page count, or BundleError.
    Everything that can refuse an entry happens here, so load_bundle can
    refuse the whole bundle before its first write."""
    missing = [f for f in _FILES if not (entry / f).is_file()]
    if missing:
        raise BundleError(f"{entry.name}: missing {', '.join(missing)}")
    try:
        result = json.loads((entry / "result.json").read_text(encoding="utf-8"))
        for r in result["rows"]:
            Characteristic.model_validate(r)
        facts = json.loads((entry / "campaign.json").read_text(encoding="utf-8"))
        if not isinstance(facts, dict):
            raise TypeError("campaign.json is not an object")
    except (ValueError, KeyError, TypeError, ValidationError, OSError) as e:
        # TYPE only: a validation message can quote a client value (CLAUDE.md §1).
        raise BundleError(f"{entry.name}: unreadable proposal ({type(e).__name__})") from None
    for k in ("campaign", "group", "file_name", "doc_hash"):
        if not isinstance(facts.get(k), str) or not facts[k]:
            raise BundleError(f"{entry.name}: campaign.json lacks {k}")
    # The dir name is what the operator selected by and campaign.json's
    # doc_hash is what grading joins on: if they disagree, one is wrong, and
    # a drawing graded under the wrong hash poisons the result silently.
    if facts["doc_hash"] != entry.name:
        raise BundleError(f"{entry.name}: campaign.json names a different doc_hash")
    try:
        with fitz.open(entry / "input.pdf") as doc:
            pages = doc.page_count
    except Exception as e:
        # PyMuPDF raises its own types (and RuntimeError) for a corrupt file;
        # its message can carry the path. Checked here, not at copy time, so
        # a corrupt drawing refuses the bundle before anything is written.
        raise BundleError(f"{entry.name}: unreadable drawing ({type(e).__name__})") from None
    return {**facts, "_pages": pages}


def load_bundle(root, data: DataDir, repo: Repo) -> dict:
    # `.`-prefixed dirs are campaign_build.py's `.<hash>.tmp` work dirs: a
    # hard kill can leave one complete, but it was never renamed into place,
    # i.e. never published, so it is not an entry.
    entries = sorted(p for p in Path(root).iterdir()
                     if p.is_dir() and not p.name.startswith("."))
    checked = [(e, _check(e)) for e in entries]
    if len({f["campaign"] for _, f in checked}) > 1:
        raise BundleError("a bundle holds exactly one campaign")
    loaded = skipped = 0
    for entry, facts in checked:
        pdf_bytes = (entry / "input.pdf").read_bytes()
        sha = hashlib.sha256(pdf_bytes).hexdigest()
        if repo.campaign_document_by_sha(facts["campaign"], sha):
            skipped += 1
            continue
        doc_id = uuid.uuid4().hex
        d = data.doc_dir(doc_id)
        d.mkdir(parents=True)
        # Files first, row last: the row is what queues a drawing, and over a
        # half-copied dir the worker would fail it -- or, holding result.json
        # but no input, make a review of a drawing with nothing to show.
        try:
            for f in _FILES:
                shutil.copyfile(entry / f, d / f)
        except OSError as e:
            shutil.rmtree(d, ignore_errors=True)
            raise BundleError(f"{entry.name}: copy failed ({type(e).__name__});"
                              f" {loaded} loaded before it, re-run to resume") from None
        repo.create_campaign_document(doc_id, facts["campaign"], facts["file_name"],
                                      facts["_pages"], sha)
        repo.enqueue(doc_id)
        loaded += 1
    return {"loaded": loaded, "skipped": skipped}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundle")
    args = ap.parse_args(argv)
    data = DataDir(os.environ.get("SINDRI_DATA_DIR") or "/data")
    conn = db.connect(data.db_path)
    db.migrate(conn)
    try:
        out = load_bundle(args.bundle, data, Repo(conn))
    except BundleError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"loaded {out['loaded']}, already present {out['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
