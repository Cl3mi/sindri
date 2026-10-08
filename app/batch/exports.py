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


def _manifest_csv(rows: list) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=MANIFEST_FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


class Exports:
    def __init__(self, repo, data, store, uploads):
        self.repo = repo
        self.data = data
        self.store = store
        # The SAME disk reserve imports are guarded by (batch design §2):
        # a multi-gigabyte mass export is exactly the write that reserve
        # exists for, and a second, independent threshold would just be a
        # second number to keep in sync with SINDRI_MIN_FREE_MB.
        self.uploads = uploads
        # One lock per export id: `start` launches a real background thread,
        # and a caller (production: a retried click; this module's own
        # tests: a direct `_run` call made right after `start` for
        # determinism) can end up invoking `_run` a second time for a job
        # already being built. Without this, both would mkstemp their own
        # temp ZIP, both would finish rendering, and the SLOWER one's temp
        # file could still be sitting in exports/ at the exact moment a
        # caller checks for it -- a real race, not a test artefact, since
        # both threads are doing genuine work concurrently. The second
        # caller to acquire the lock sees the job already left `running`
        # and returns without creating anything.
        self._run_locks: dict = {}
        self._run_locks_guard = threading.Lock()

    def _run_lock(self, export_id) -> threading.Lock:
        with self._run_locks_guard:
            return self._run_locks.setdefault(export_id, threading.Lock())

    def start(self, document_ids: list) -> dict:
        job = self.repo.create_export(document_ids)
        t = threading.Thread(target=self._run, args=(job["id"], list(document_ids)),
                             name=f"sindri-export-{job['id'][:8]}", daemon=True)
        t.start()
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
        with self._run_lock(export_id):
            if self.repo.get_export(export_id)["state"] != "running":
                # A concurrent call already built (or failed) this exact
                # job while we waited for the lock -- nothing left to do.
                return
            self._run_locked(export_id, document_ids)

    def _run_locked(self, export_id, document_ids) -> None:
        zip_path = self.data.export_zip_path(export_id)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.data.exports, prefix=f"{export_id}.", suffix=".tmp.zip")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
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
            # A fault building the ZIP ITSELF (a permissions error, a bug) --
            # not a single drawing's render fault, which _export_one already
            # turned into a manifest row instead of raising. TYPE only
            # (CLAUDE.md §1).
            log.warning("mass export %s failed: %s", export_id[:8], type(e).__name__)
            self._finish(export_id, "failed", error_type=type(e).__name__)
        finally:
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
        for p in self.data.exports.glob("*.zip"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    n += 1
            except OSError as e:
                log.warning("export zip cleanup: %s", type(e).__name__)
        return n
