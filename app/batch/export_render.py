"""The single place a sealed review revision becomes an xlsx or a ballooned
PDF (batch design §4). Moved here from app/main.py unchanged: app.main's
single-document export route and app.batch.exports' mass export both call
render_export, under the SAME per-(doc, rev, kind) cache and lock, so a
drawing exported alone and the same drawing inside a mass-export ZIP are
byte-identical by construction, not by coincidence."""
import json
import os
import tempfile
import threading
from pathlib import Path

import fitz  # PyMuPDF

from app.excel import write_workbook
from app.models import Characteristic, MarkBlock, NoteBlock, TitleField
from app.pipeline.ballooned_pdf import render_ballooned_pdf


def exportable(rows):
    """Confirmed rows only. The UI already omits unconfirmed suggestions; this
    is the server-side guarantee, because an unconfirmed value in the client's
    Excel or ballooned PDF is exactly the failure the drops exist to prevent
    (docs/plans/2026-10-07-suggestion-tray-design.md)."""
    return [r for r in rows if not r.suggested]


class InputUnavailable(Exception):
    """One of the export's INPUTS -- the sealed rows, result.json, or
    input.pdf -- is missing or unreadable. Raised instead of letting the
    underlying OSError/ValueError/KeyError/RuntimeError escape, so
    export_document can tell this apart from a fault in the EXPORTER itself
    (app.excel / app.pipeline.ballooned_pdf): that is a bug and must 500, not
    be reported as "the files are gone"."""


# One lock per (doc_id, rev, kind): two requests racing the FIRST export of a
# revision must not share a fixed temp name -- the loser's rename would raise
# FileNotFoundError on what was otherwise a perfectly good export, or (worse)
# a half-written file could be renamed into place and served. The dict itself
# is touched from more than one thread, hence its own small guard.
_EXPORT_LOCKS: dict = {}
_EXPORT_LOCKS_GUARD = threading.Lock()


def _export_lock(key) -> threading.Lock:
    with _EXPORT_LOCKS_GUARD:
        return _EXPORT_LOCKS.setdefault(key, threading.Lock())


def render_export(doc_id: str, work: Path, rev: int, kind: str, sealed: dict) -> Path:
    """Render revision `rev` once and cache it, so a repeated or mass export
    of the same revision is the same bytes (batch design §4). The rows come
    from the SEALED revision, never from a browser.

    `mark_exported` (in the caller) runs on every call, cache hit or not: a
    re-download still IS an export, and an explicit older `?rev=` moving
    `exported_revision` backward is correct -- that field means "last
    exported", not "highest ever sealed". The cached rN.* is never
    invalidated if the exporter's own code changes later; delete
    docs/<id>/exports/ by hand to force a rebuild."""
    out = work / "exports" / f"r{rev}.{kind}"
    if out.is_file():
        return out
    with _export_lock((doc_id, rev, kind)):
        if out.is_file():      # a concurrent first render finished while we waited
            return out
        if not work.is_dir():
            # The row can outlive its files (a delete race, and routinely
            # after a future retention purge). Without this check
            # `mkdir(exist_ok=True)` below would find its own parent gone
            # and raise FileNotFoundError -- an input fault, not a bug -- and
            # recreating docs/<id>/ just to hold an empty exports/ directory
            # would be exactly the orphan the row-must-exist-first rule
            # elsewhere in this module exists to avoid.
            raise InputUnavailable("document directory is gone")
        out.parent.mkdir(exist_ok=True)
        try:
            rows = exportable([Characteristic.model_validate(r) for r in sealed["rows"]])
            notes = marks = title_block = None
            src = None
            if kind == "xlsx":
                result = json.loads((work / "result.json").read_text(encoding="utf-8"))
                notes = NoteBlock.model_validate(result["notes"]) if result["notes"] else None
                marks = MarkBlock.model_validate(result["marks"]) if result["marks"] else None
                title_block = [TitleField.model_validate(t) for t in result["title_block"]]
            else:
                src = work / "input.pdf"
                if not src.is_file():
                    raise InputUnavailable("input.pdf is missing")
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise InputUnavailable from e

        # A unique name per attempt -- the single fixed temp name this used
        # to use let a second concurrent render clobber or lose the first's
        # file. Removed in `finally` whether or not the rename below ran (it
        # is already gone once `replace` succeeds; unlink then no-ops).
        fd, tmp_name = tempfile.mkstemp(
            dir=out.parent, prefix=f"r{rev}.", suffix=f".tmp.{kind}")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            if kind == "xlsx":
                write_workbook(rows, tmp, notes=notes, marks=marks, title_block=title_block)
            else:
                try:
                    render_ballooned_pdf(src, rows, dpi=300, out_path=tmp)
                except (fitz.FileDataError, fitz.FileNotFoundError) as e:
                    # PyMuPDF's own exceptions for a missing/corrupt PDF are
                    # RuntimeError subclasses, not OSError -- without this
                    # they would escape as an unhandled 500 with a traceback
                    # naming the full path.
                    raise InputUnavailable from e
            tmp.replace(out)
        finally:
            tmp.unlink(missing_ok=True)
        return out
