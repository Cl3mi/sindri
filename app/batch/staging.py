"""Staging rules (batch design §2, decisions 19 and 26) and the shapes the
staging API returns.

One module, so the Include default the repo STORES and the flags the API
SHOWS come from the same functions: at staging time, a row is never
excluded by default for a reason the staging view does not display. (Only
at staging time: Include is stored once, then it is the user's. A copy
whose original is later removed loses its duplicate badge but keeps the
Include it was staged with.)"""
import json
import unicodedata

from app.batch.paths import has_refused_chars
from app.batch.unzip import summarise_ignored

MAX_BATCH_NAME = 200


def out_of_scope(pages, oversized) -> bool:
    """Multi-page (only sheet 1 is read) or oversized (sheet 1 is read at a
    reduced resolution) -- decision 26. Unknown (None) is NOT out of scope:
    that is a row staged before B2, whose sheet was never measured."""
    return bool((pages or 0) > 1 or oversized)


def default_included(*, unreadable, pages, oversized, duplicate) -> bool:
    """On for a normal file. Off for an out-of-scope file and for a
    duplicate, which the user may still include; off for an unreadable PDF,
    which can never be included."""
    return not (unreadable or duplicate or out_of_scope(pages, oversized))


def clean_batch_name(name):
    """The name NFC-normalised and stripped, or None if it cannot be a
    batch name: empty, longer than MAX_BATCH_NAME, or carrying a character
    paths.safe_rel_parts refuses in a folder name -- control (incl. NEL),
    format (bidi overrides, zero-width) or line/paragraph separators. It
    becomes a folder name in the mass-export ZIP (design §4), so it obeys
    the same rule as the folders a ZIP or folder upload brings in. NFC so a
    name typed on macOS and elsewhere is one name. (Leading and trailing
    whitespace, which str.strip() also takes NEL and U+2028 to be, is
    stripped rather than refused.)"""
    if not isinstance(name, str):
        return None
    s = unicodedata.normalize("NFC", name).strip()
    if not s or len(s) > MAX_BATCH_NAME or has_refused_chars(s):
        return None
    return s


def batch_view(b) -> dict:
    return {"id": b["id"], "name": b["name"], "draft": bool(b["draft"]),
            "created_at": b["created_at"]}


def upload_view(u) -> dict:
    ignored = json.loads(u["ignored"]) if u["ignored"] else []
    if isinstance(ignored, list):
        # Stored before the list was capped (and still what a test fixture
        # passes): every entry is listed, so it counts itself.
        ignored = summarise_ignored(ignored)
    return {"id": u["id"], "name": u["name"], "rel_path": u["rel_path"],
            "size": u["size"], "received": u["received"], "state": u["state"],
            # A hash of the file's ends (never client text): the interim UI
            # checks a re-selected file against a completed upload with it,
            # since name, size and mtime survive `cp -p` onto other content.
            "fingerprint": u["fingerprint"],
            "kind": u["kind"], "ignored": ignored["listed"],
            "ignored_total": ignored["total"], "ignored_by_reason": ignored["by_reason"]}


def staging_row(r) -> dict:
    """A document row joined with its duplicate's facts (Repo._STAGING_SELECT)."""
    dup = None
    if r["dup_id"] is not None:
        dup = {"id": r["dup_id"], "file_name": r["dup_file_name"],
               "batch_id": r["dup_batch_id"], "batch_name": r["dup_batch_name"],
               "state": r["dup_state"]}
    return {
        "id": r["id"], "batch_id": r["batch_id"], "upload_id": r["upload_id"],
        "file_name": r["file_name"], "rel_path": r["rel_path"], "size": r["size"],
        "pages": r["pages"],
        "oversized": None if r["oversized"] is None else bool(r["oversized"]),
        "unreadable": bool(r["unreadable"]),
        "out_of_scope": out_of_scope(r["pages"], r["oversized"]),
        "duplicate_of": dup,
        "included": bool(r["included"]),
        "allowed_out_of_scope": bool(r["allowed_out_of_scope"]),
        "state": r["state"],
    }


def staging_counts(rows, uploads) -> dict:
    """Cross-checkable by construction: included + excluded == documents ==
    len(rows), and every other count is a subset of rows (or of uploads)."""
    included = sum(r["included"] for r in rows)
    return {
        "documents": len(rows),
        "included": included,
        "excluded": len(rows) - included,
        "unreadable": sum(r["unreadable"] for r in rows),
        "duplicates": sum(r["duplicate_of"] is not None for r in rows),
        "out_of_scope": sum(r["out_of_scope"] for r in rows),
        "uploading": sum(u["state"] != "complete" for u in uploads),
    }
