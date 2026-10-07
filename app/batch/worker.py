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
                 idle_wait: float = 2.0, startup: Optional[Callable] = None):
        self.repo = repo
        self.data = data
        self.store = store
        self.extract_fn = extract_fn     # (pdf_path, work_dir, progress) -> ExtractionResult
        self.header_fn = header_fn       # (pdf_path) -> review header dict
        self.idle_wait = idle_wait
        # Run inside start()'s locked section, right after the flock is
        # taken: the one moment this process is known to be the data dir's
        # only worker. Runtime passes import recovery (uploads.recover).
        # ONCE per worker: a later start() (after a stop) happens while
        # requests are served, when every marked dir is live work.
        self.startup = startup
        self._recovered = False
        self._progress = {}              # doc_id -> latest report (UI only, never logged)
        self._current = None
        self._wake = threading.Event()
        # Replaced per loop by start(): each loop watches its OWN event, so a
        # later start() can never clear the stop request of a loop that is
        # still finishing a drawing and revive it next to the new one.
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

    def progress_of(self, doc_id) -> Optional[dict]:
        """A COPY of the latest progress report, None when not running: API
        threads read it while the worker thread replaces it."""
        p = self._progress.get(doc_id)
        return dict(p) if p is not None else None

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

    def _state(self, doc_id: str) -> str:
        """The row's state; a removed drawing raises Cancelled."""
        try:
            return self.repo.get(doc_id)["state"]
        except UnknownDocument:
            raise Cancelled() from None

    def _run(self, doc_id: str) -> None:
        d = self.data.doc_dir(doc_id)
        result_path = d / "result.json"

        def progress(step, detail="", current=None, total=None):
            if self._state(doc_id) == "cancelled":
                raise Cancelled()
            self._progress[doc_id] = {"step": step, "detail": detail,
                                      "current": current, "total": total}

        try:
            if not result_path.is_file():
                result = self.extract_fn(d / "input.pdf", d, progress)
                # Removed after the last progress report: nothing of it is
                # written back (_state raises Cancelled).
                self._state(doc_id)
                # FIRST, and atomically: a later review-store fault -- or a
                # cancel that came too late to save GPU time -- is then
                # retried from this file without re-running the GPU.
                write_json_atomic(result_path, result_payload(result))
            # Cancelled after the last report: the user's decision stands, so
            # no review record, but result.json stays for a cheap Retry.
            if self._state(doc_id) == "cancelled":
                raise Cancelled()
        except Cancelled:
            self._settle(doc_id, self.repo.mark_cancelled)
            return
        except Exception as e:
            # TYPE only: a pipeline message can carry client text (CLAUDE.md §1).
            log.warning("extraction failed for %s: %s", doc_id[:8], type(e).__name__)
            self._settle(doc_id, self.repo.mark_failed, type(e).__name__)
            return
        finally:
            self._progress.pop(doc_id, None)
        try:
            rows = json.loads(result_path.read_text(encoding="utf-8"))["rows"]
        except (ValueError, KeyError, TypeError) as e:
            # An unreadable result.json would fail every Retry forever, since
            # the retry path trusts the file instead of the GPU. Discard it,
            # so the next Retry extracts afresh.
            log.warning("result unreadable for %s: %s", doc_id[:8], type(e).__name__)
            result_path.unlink(missing_ok=True)
            self._settle(doc_id, self.repo.mark_failed, f"review_store:{type(e).__name__}")
            return
        except OSError as e:
            # A transient read fault is not corruption: keep the file for a
            # GPU-free Retry, but fail the drawing rather than strand it
            # `extracting` until the next restart.
            log.warning("result unreadable for %s: %s", doc_id[:8], type(e).__name__)
            self._settle(doc_id, self.repo.mark_failed, f"review_store:{type(e).__name__}")
            return
        try:
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
        except Exception as e:
            # A database fault (locked, disk full). The row stays `extracting`
            # until the next start() requeues it; logged here by TYPE rather
            # than escaping into the loop as an unexplained traceback.
            log.error("could not settle document %s: %s", doc_id[:8], type(e).__name__)

    # ----- background loop ----------------------------------------------
    def start(self) -> None:
        """Not thread-safe: call start()/stop() from one thread (the app's
        lifespan does)."""
        if self._thread is not None:
            if self._thread.is_alive():
                if not self._stop.is_set():
                    return                       # already running
                # A stop() timed out mid-drawing; a second loop now would run
                # beside it. We still hold the lock for it.
                raise RuntimeError(
                    "the previous extraction loop is still finishing its drawing")
            # That loop has since returned: let go of its lock and start clean.
            self._thread = None
            self._release_lock()
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
        try:
            if self.startup is not None and not self._recovered:
                # Recovery only deletes leftovers of interrupted imports; a
                # fault in it must not keep the worker -- and every queued
                # drawing -- from starting. TYPE only (CLAUDE.md §1). Marked
                # as run even if it failed: retrying it on a restart would
                # run it while requests are served.
                self._recovered = True
                try:
                    self.startup()
                except Exception as e:
                    log.warning("startup recovery failed: %s", type(e).__name__)
            moved = self.repo.requeue_interrupted()
            if moved["requeued"] or moved["failed"]:
                log.info("startup: requeued %d, failed %d interrupted extraction(s)",
                         moved["requeued"], moved["failed"])
            stop = threading.Event()
            thread = threading.Thread(target=self._loop, args=(stop,),
                                      name="sindri-worker", daemon=True)
            thread.start()
        except BaseException:
            # Otherwise the fd and its lock leak, and a retry is refused as if
            # another process held the worker.
            f.close()
            raise
        self._lockfile = f
        self._stop = stop
        self._thread = thread

    def _loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                ran = self.run_once()
            except Exception as e:
                log.error("worker loop: %s", type(e).__name__)
                ran = None
            if ran is None:
                self._wake.wait(self.idle_wait)
                self._wake.clear()

    def stop(self, timeout: float = 5.0) -> None:
        """Not thread-safe: call start()/stop() from one thread (the app's
        lifespan does)."""
        self._stop.set()
        self._wake.set()
        if self._thread is None:
            return
        # A drawing mid-extraction (~15 GPU minutes) is not waited for. Its
        # loop keeps running until the drawing settles, so this worker keeps
        # the thread AND the lock: releasing the lock now would let a second
        # worker requeue the in-flight row and extract it twice. Only if the
        # process exits does the row stay `extracting` for the next process's
        # start() to requeue.
        self._thread.join(timeout)
        if self._thread.is_alive():
            log.warning("worker stop: loop still extracting; lock kept until it returns")
            return
        self._thread = None
        self._release_lock()

    def _release_lock(self) -> None:
        if self._lockfile is not None:
            self._lockfile.close()
            self._lockfile = None
