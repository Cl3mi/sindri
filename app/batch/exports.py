"""Mass export (batch design §4): a selection, a batch, or a filtered set of
finished drawings becomes one ZIP -- per-drawing xlsx + ballooned PDF laid
out by batch/folder, plus a manifest.csv that accounts for every drawing
asked for -- built on its own thread so it never queues behind the GPU
worker. Renders through app.batch.export_render.render_export, the SAME
function (and the same per-(doc, rev, kind) cache and lock) a lone download
uses, so a drawing exported alone and the same drawing inside this ZIP are
byte-identical by construction."""
import csv
import io
import logging
import os
import re
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from app.batch.export_render import InputUnavailable, exportable, render_export
from app.batch.names import unique_export_path, windows_safe_component
from app.batch.repo import InvalidTransition, UnknownDocument
from app.batch.uploads import DiskFull
from app.models import Characteristic

log = logging.getLogger(__name__)

MANIFEST_FIELDS = ["file", "folder", "batch", "state", "revision_exported",
                   "sealed_at", "rows_exported", "allowed_out_of_scope", "skip_reason"]

# ZIPs are rebuildable from the same selection (batch design §4): deleting
# one after a day costs nothing but a re-click, and keeping every mass
# export forever would grow without bound on a server nobody is asked to
# clean up by hand.
ZIP_TTL_SECONDS = 24 * 3600

# A FINAL export ZIP's name -- never an in-progress build's own
# "<id>.<rand>.tmp.zip" (produced by _run's tempfile.mkstemp), which a plain
# "*.zip" glob would also match. delete_expired_zips must never remove a
# live temp file out from under a long-running build just because it is
# older than the TTL; fail_interrupted, not this periodic sweep, owns
# cleaning up a temp file whose job has actually ended.
_FINAL_ZIP_RE = re.compile(r"\A[0-9a-f]{32}\.zip\Z")

# Spreadsheet formula injection (CSV, not CWE-1236's HTML-escaping cousin):
# batch names, rel_path folders and file stems all come from a client's
# title block or an uploaded filename, and manifest.csv is opened in a
# spreadsheet program by an operator. Any of these leading characters make
# the cell a live formula the instant the sheet opens -- defused with a
# leading single quote, which every major spreadsheet treats as "this cell
# is text", exactly as it already does for a user-typed leading apostrophe.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    if isinstance(value, str) and value.startswith(_FORMULA_LEAD):
        return "'" + value
    return value


def _manifest_csv(rows: list) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=MANIFEST_FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: _csv_safe(v) for k, v in r.items()})
    return buf.getvalue().encode("utf-8")


def _spawn_thread(fn, *args) -> None:
    """Default `spawn`: a plain daemon thread, exactly what every production
    caller gets. Tests inject `spawn=lambda fn, *a: fn(*a)` to run a job
    synchronously -- the double _run-for-one-job race that used to need a
    per-export-id lock here only ever came from a test calling `_run`
    directly on top of `start`'s own thread; production never does that
    (every `start()` mints a fresh export id), so the lock was pure
    unbounded-growth overhead with no corresponding real risk."""
    export_id = args[0] if args else None
    name = f"sindri-export-{export_id[:8]}" if export_id else "sindri-export"
    threading.Thread(target=fn, args=args, name=name, daemon=True).start()


class Exports:
    def __init__(self, repo, data, store, uploads, spawn=None):
        self.repo = repo
        self.data = data
        self.store = store
        # The SAME disk reserve imports are guarded by (batch design §2):
        # a multi-gigabyte mass export is exactly the write that reserve
        # exists for, and a second, independent threshold would just be a
        # second number to keep in sync with SINDRI_MIN_FREE_MB.
        self.uploads = uploads
        # How `_run` actually gets executed: a daemon thread in production
        # (`_spawn_thread`), or a direct synchronous call in tests (`spawn=
        # lambda fn, *a: fn(*a)`), so a test's `start()` call returns only
        # once the job has actually finished -- no sleeping, no polling, no
        # need to also reach for the private `_run` method.
        self._spawn = spawn or _spawn_thread

    def start(self, document_ids: list) -> dict:
        job = self.repo.create_export(document_ids)
        self._spawn(self._run, job["id"], list(document_ids))
        return job

    def status(self, export_id) -> dict:
        return self.repo.get_export(export_id)

    def fail_interrupted(self) -> None:
        """At startup (wired into the Worker's startup hook, Task 6): a job
        still `running` was cut off by a crash or restart. Unlike a GPU
        extraction, there is no result.json equivalent to resume from -- the
        thread that was building the ZIP is simply gone -- so each is marked
        `failed` and its half-written temp ZIP (the exact name `_run`'s own
        tempfile.mkstemp produces: "<id>.<random>.tmp.zip") is removed, so a
        stale .tmp.zip never lingers in exports/ forever."""
        for export_id in self.repo.running_exports():
            self._finish(export_id, "failed", error_type="interrupted")
            for tmp in self.data.exports.glob(f"{export_id}.*.tmp.zip"):
                tmp.unlink(missing_ok=True)

    def _finish(self, export_id, state, error_type=None) -> None:
        """finish_export, tolerant of a job that is no longer `running`: a
        fault here must never escape -- fail_interrupted and a build thread's
        own terminal call can in principle race the same job (B4's carried
        note from Task 4), and whichever loses must simply do nothing rather
        than crash a background thread with no caller to see the exception.
        Type only, never a client name (CLAUDE.md §1)."""
        try:
            self.repo.finish_export(export_id, state, error_type=error_type)
        except InvalidTransition:
            log.warning("export %s already finished elsewhere", export_id[:8])

    def _run(self, export_id, document_ids) -> None:
        zip_path = self.data.export_zip_path(export_id)
        tmp = None
        try:
            # mkstemp itself can raise (e.g. ENOSPC) -- it must be INSIDE
            # the try, or that exception would propagate out of the thread
            # with nothing ever marking the job `failed`: a `running` row
            # with no worker left to finish it, frozen forever, the same
            # failure mode decision 2 exists to prevent.
            fd, tmp_name = tempfile.mkstemp(
                dir=self.data.exports, prefix=f"{export_id}.", suffix=".tmp.zip")
            os.close(fd)
            tmp = Path(tmp_name)
            self.uploads.guard_disk()
            used_paths = set()
            rows = []
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
                for i, doc_id in enumerate(document_ids):
                    # Checked between drawings too, not only once at the
                    # start: a long job exporting many large drawings must
                    # not be allowed to fill the disk one drawing at a time.
                    self.uploads.guard_disk()
                    rows.append(self._export_one(zf, doc_id, used_paths))
                    self.repo.update_export_progress(export_id, i + 1)
                zf.writestr("manifest.csv", _manifest_csv(rows))
            tmp.replace(zip_path)       # atomic: no reader ever sees a partial ZIP
            self._finish(export_id, "done")
        except DiskFull:
            # The 507-equivalent outcome for a background job, which has no
            # HTTP response to answer 507 on -- a fixed error_type string
            # rather than the exception's own class name, so a future rename
            # of DiskFull can never silently change what the manifest/DB say.
            log.warning("mass export %s failed: disk reserve reached", export_id[:8])
            self._finish(export_id, "failed", error_type="OutOfDisk")
        except Exception as e:
            # A fault building the ZIP ITSELF (a permissions error, a bug,
            # or mkstemp failing before `tmp` even exists) -- not a single
            # drawing's render fault, which _export_one already turned into
            # a manifest row instead of raising. TYPE only (CLAUDE.md §1).
            log.warning("mass export %s failed: %s", export_id[:8], type(e).__name__)
            self._finish(export_id, "failed", error_type=type(e).__name__)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)

    def _export_one(self, zf, doc_id, used_paths) -> dict:
        """One manifest row. Never raises: a fault here must not fail the
        whole job (batch design §5) -- it is recorded as an export_error
        skip_reason instead."""
        base = {"file": "", "folder": "", "batch": "", "state": "removed",
                "revision_exported": "", "sealed_at": "", "rows_exported": "",
                "allowed_out_of_scope": "", "skip_reason": "not_finished"}
        try:
            doc = self.repo.get(doc_id)
        except UnknownDocument:
            return base
        batch = self.repo.get_batch(doc["batch_id"])
        folder_parts = [windows_safe_component(batch["name"])]
        if doc["rel_path"]:
            folder_parts += [windows_safe_component(p) for p in doc["rel_path"].split("/") if p]
        stem = windows_safe_component(Path(doc["file_name"]).stem) or "drawing"
        base = {**base, "folder": "/".join(folder_parts), "batch": batch["name"],
                "state": doc["state"], "allowed_out_of_scope": bool(doc["allowed_out_of_scope"]),
                "skip_reason": ""}
        if doc["final_revision"] is None:
            # Never sealed: eligibility is "has a final_revision", exactly
            # the rule the single-document export route already applies,
            # never "current state is finished" (decision 5 -- a reopened
            # drawing's final_revision survives the reopen).
            reason = {"failed": "failed", "purged": "purged"}.get(doc["state"], "not_finished")
            return {**base, "skip_reason": reason}
        try:
            sealed = self.store.revision(doc_id, doc["final_revision"])
            work = self.data.doc_dir(doc_id)
            n_rows = len(exportable([Characteristic.model_validate(r) for r in sealed["rows"]]))
            xlsx_name = unique_export_path(used_paths, folder_parts, stem, ".xlsx")
            pdf_name = unique_export_path(used_paths, folder_parts, f"{stem}_ballooned", ".pdf")
            xlsx_path = render_export(doc_id, work, doc["final_revision"], "xlsx", sealed)
            pdf_path = render_export(doc_id, work, doc["final_revision"], "pdf", sealed)
            zf.write(xlsx_path, arcname=xlsx_name)
            zf.write(pdf_path, arcname=pdf_name)
            # Harmless if retention's purge() races this between the render
            # above and this call: it only bumps exported_at/exported_revision
            # on a row that is about to become (or already is) a tombstone,
            # and purge_candidates/purge only ever read `finished` rows, so a
            # stray bump on a `purged` one is simply never looked at again.
            self.repo.mark_exported(doc_id, doc["final_revision"])
            # A row still `finished` was exported cleanly; any other state
            # with a final_revision got here only by being reopened after a
            # prior Finish (decision 5) -- its CURRENT content was never
            # sealed, so the file the ZIP holds is its LAST Finish, not what
            # the drawing looks like right now.
            note = "" if doc["state"] == "finished" else "reopened — later changes not included"
            return {**base, "file": Path(xlsx_name).name, "revision_exported": doc["final_revision"],
                    "sealed_at": sealed["sealed_at"], "rows_exported": n_rows, "skip_reason": note}
        except InputUnavailable as e:
            cause = type(e.__cause__ or e).__name__
            return {**base, "revision_exported": doc["final_revision"],
                    "skip_reason": f"export_error: {cause}"}
        except Exception as e:
            log.warning("mass export: drawing %s failed to render: %s",
                        doc_id[:8], type(e).__name__)
            return {**base, "revision_exported": doc["final_revision"],
                    "skip_reason": f"export_error: {type(e).__name__}"}

    def delete_expired_zips(self) -> int:
        cutoff = time.time() - ZIP_TTL_SECONDS
        n = 0
        for p in self.data.exports.iterdir():
            # "*.zip" would also match a live "<id>.<rand>.tmp.zip" build --
            # only a FINAL "<32 hex>.zip" name is ever a candidate here.
            if not _FINAL_ZIP_RE.fullmatch(p.name):
                continue
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    n += 1
            except OSError as e:
                log.warning("export zip cleanup: %s", type(e).__name__)
        return n
