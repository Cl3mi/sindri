"""Retention and export-ZIP cleanup (batch design §4): a second always-on
background thread, alongside the extraction Worker, owned by Runtime and
started only after Worker.start() has won the single-process lock -- the
same cross-process guarantee B2's import recovery relies on, reused without
touching Worker itself (Housekeeping has nothing to do with the GPU queue).
It sweeps once immediately, satisfying "one at startup", then on a fixed
interval -- short enough that an old drawing does not wait long past its
configured retention window, with no separate "has a day passed" bookkeeping
needed, because a sweep that finds nothing to purge is cheap."""
import logging
import shutil
import threading
from datetime import timedelta

log = logging.getLogger(__name__)

# How often the loop wakes to check. Not "daily" literally -- see the
# module docstring: there is no harm in checking more often than that.
_TICK_SECONDS = 3600


class Housekeeping:
    def __init__(self, repo, data, exports, retention_days: int, tick_seconds=_TICK_SECONDS):
        self.repo = repo
        self.data = data
        self.exports = exports
        self.retention_days = retention_days
        self._tick = tick_seconds
        self._thread = None
        self._stop = threading.Event()

    def sweep_retention(self) -> dict:
        """0 = off (decision 7): no cutoff is computed, nothing is purged."""
        if self.retention_days <= 0:
            return {"purged": 0}
        cutoff = (self.repo.now() - timedelta(days=self.retention_days)).isoformat(
            timespec="microseconds")
        purged = sum(1 for doc_id in self.repo.purge_candidates(cutoff)
                    if self.purge_one(doc_id, cutoff))
        return {"purged": purged}

    def purge_one(self, doc_id, cutoff) -> bool:
        """Mark the row purged FIRST, delete its files SECOND -- never the
        other way round (repo.purge's own docstring explains why: a request
        racing this sweep must see `purged` the instant the row says so,
        never pass a state check and then find its files gone). Review
        records are never touched here -- they live under SINDRI_REVIEW_DIR,
        a separate tree this module never imports a path into."""
        if not self.repo.purge(doc_id, cutoff):
            return False
        try:
            shutil.rmtree(self.data.doc_dir(doc_id))
        except FileNotFoundError:
            pass  # already gone -- nothing left to delete, not a fault
        except Exception as e:
            # The row already says `purged`: a filesystem fault here must
            # not stop the sweep from moving on to the next candidate, and
            # must never leak a path or message into the log (CLAUDE.md §4).
            log.warning("purge file delete failed for %s: %s",
                       doc_id[:8], type(e).__name__)
        return True

    def sweep_export_zips(self) -> int:
        return self.exports.delete_expired_zips()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._run_once()
        self._thread = threading.Thread(
            target=self._loop, name="sindri-housekeeping", daemon=True)
        self._thread.start()

    def _run_once(self) -> None:
        try:
            self.sweep_retention()
        except Exception as e:
            log.warning("retention sweep failed: %s", type(e).__name__)
        try:
            self.sweep_export_zips()
        except Exception as e:
            log.warning("export zip cleanup failed: %s", type(e).__name__)

    def _loop(self) -> None:
        while not self._stop.wait(self._tick):
            self._run_once()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
