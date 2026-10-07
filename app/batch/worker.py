"""The extraction worker (batch design §1): one drawing at a time, in the
background, with no browser attached.

A cancel is read from the index at every progress report, not from an
in-memory flag: a flag set for a QUEUED drawing would otherwise outlive the
cancel and kill that drawing's run the moment a later Retry started it.

Cancel is cooperative, so the run goes on until its next progress report
after the row already says `cancelled` -- and a cancelled row may be
deleted. A drawing can therefore be REMOVED while its extraction is still
physically running. A vanished row is treated as a cancel, and nothing of
that drawing is written back: no result.json, no review record."""
import fcntl
import json
import logging
import shutil
import threading
from typing import Callable, Optional

from app.batch.repo import InvalidTransition, UnknownDocument
from app.review.store import write_json_atomic

log = logging.getLogger(__name__)


class Cancelled(Exception):
    """Raised from the progress callback to unwind extract() cooperatively."""


def result_payload(result) -> dict:
    """Dumped once in json mode, so proposal.json and every later /open serve
    byte-identical rows. Suggestions ride in the same list, flagged
    (suggestion-tray design)."""
    return {
        "rows": [r.model_dump(mode="json")
                 for r in [*result.characteristics, *result.suggestions]],
        "notes": result.notes.model_dump(mode="json") if result.notes is not None else None,
        "marks": result.marks.model_dump(mode="json") if result.marks is not None else None,
        "title_block": [t.model_dump(mode="json") for t in result.title_block],
    }


class Worker:
    def __init__(self, repo, data, store, extract_fn: Callable, header_fn: Callable,
                 idle_wait: float = 2.0):
        self.repo = repo
        self.data = data
        self.store = store
        self.extract_fn = extract_fn     # (pdf_path, work_dir, progress) -> ExtractionResult
        self.header_fn = header_fn       # (pdf_path) -> review header dict
        self.idle_wait = idle_wait
        self.progress = {}               # doc_id -> latest progress report (UI only, never logged)
        self._current = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._lockfile = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def current(self) -> Optional[str]:
        """The drawing being extracted right now, None when idle. The API
        refuses to delete this one: its row may already say `cancelled` while
        the run is still going (module docstring)."""
        return self._current

    def wake(self) -> None:
        self._wake.set()

    def run_once(self) -> Optional[str]:
        doc = self.repo.claim_next()
        if doc is None:
            return None
        self._current = doc["id"]
        try:
            self._run(doc["id"])
        finally:
            self._current = None
        return doc["id"]

    def _still_wanted(self, doc_id: str) -> None:
        """Raise Cancelled if the user cancelled -- or removed -- the drawing."""
        try:
            state = self.repo.get(doc_id)["state"]
        except UnknownDocument:
            raise Cancelled() from None
        if state == "cancelled":
            raise Cancelled()

    def _run(self, doc_id: str) -> None:
        d = self.data.doc_dir(doc_id)
        result_path = d / "result.json"

        def progress(step, detail="", current=None, total=None):
            self._still_wanted(doc_id)
            self.progress[doc_id] = {"step": step, "detail": detail,
                                     "current": current, "total": total}

        try:
            if not result_path.is_file():
                result = self.extract_fn(d / "input.pdf", d, progress)
                # A cancel or remove after the last progress report still
                # wins: nothing is written for a drawing the user let go of.
                self._still_wanted(doc_id)
                # FIRST, and atomically: a later review-store fault is then
                # retried from this file without re-running the GPU.
                write_json_atomic(result_path, result_payload(result))
        except Cancelled:
            self._settle(doc_id, self.repo.mark_cancelled)
            return
        except Exception as e:
            # TYPE only: a pipeline message can carry client text (CLAUDE.md §1).
            log.warning("extraction failed for %s: %s", doc_id[:8], type(e).__name__)
            self._settle(doc_id, self.repo.mark_failed, type(e).__name__)
            return
        finally:
            self.progress.pop(doc_id, None)
        try:
            rows = json.loads(result_path.read_text(encoding="utf-8"))["rows"]
            self.store.create(doc_id, self.header_fn(d / "input.pdf"), {"rows": rows})
        except Exception as e:
            # The journal is mandatory (decision 30): a drawing that cannot be
            # reviewed durably is never shown as ready.
            log.warning("review record failed for %s: %s", doc_id[:8], type(e).__name__)
            self._settle(doc_id, self.repo.mark_failed, f"review_store:{type(e).__name__}")
            return
        self._settle(doc_id, self.repo.mark_ready)

    def _settle(self, doc_id, transition, *args) -> None:
        try:
            transition(doc_id, *args)
        except InvalidTransition:
            # Cancelled while it ran: the user's decision stands.
            log.info("document %s changed state while extracting; left as is", doc_id[:8])
        except UnknownDocument:
            # Removed while it ran, after the last check: whatever this run
            # wrote in the meantime (result.json, a review record) belongs to
            # a drawing that no longer exists, and is client data -- purge it.
            log.info("document %s removed while extracting", doc_id[:8])
            shutil.rmtree(self.data.doc_dir(doc_id), ignore_errors=True)
            shutil.rmtree(self.store.root / doc_id, ignore_errors=True)

    # ----- background loop ----------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        # Cross-process guard: a second uvicorn worker would run a second
        # extraction loop on the same GPU and the same queue.
        f = open(self.data.root / "worker.lock", "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise RuntimeError(
                "another process already runs the extraction worker for this data "
                "dir; run uvicorn as a single worker") from None
        self._lockfile = f
        moved = self.repo.requeue_interrupted()
        if moved["requeued"] or moved["failed"]:
            log.info("startup: requeued %d, failed %d interrupted extraction(s)",
                     moved["requeued"], moved["failed"])
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="sindri-worker", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                ran = self.run_once()
            except Exception as e:
                log.error("worker loop: %s", type(e).__name__)
                ran = None
            if ran is None:
                self._wake.wait(self.idle_wait)
                self._wake.clear()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            # A drawing mid-extraction is NOT waited for: it stays
            # `extracting` and the next start() requeues it.
            self._thread.join(timeout)
            self._thread = None
        if self._lockfile is not None:
            self._lockfile.close()
            self._lockfile = None
