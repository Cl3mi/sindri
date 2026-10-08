"""Wires a deployment together (batch design §1): data dir, index, review
store and the extraction worker."""
import logging
import os
from pathlib import Path

from app.batch.db import connect, migrate
from app.batch.exports import Exports
from app.batch.housekeeping import Housekeeping
from app.batch.paths import DataDir
from app.batch.repo import Repo, UnknownDocument
from app.batch.uploads import MIB, Uploads
from app.batch.worker import Worker
from app.review.store import ReviewStore

log = logging.getLogger(__name__)

# The free-disk reserve (batch design §2): below it, uploads and unpacking
# answer 507 so an import can never fill the disk under a review journal.
DEFAULT_MIN_FREE_MB = 2048

# How long a finished, exported drawing's files are kept before retention
# deletes them (decision 7); 0 turns retention off.
DEFAULT_RETENTION_DAYS = 30


def _probe_writable(root: Path) -> None:
    # mkdir on an existing read-only mount succeeds silently; only a real
    # write proves the journal can be kept -- and it is mandatory now
    # (decision 30), so failing here fails the boot, loudly.
    probe = root / f".write-probe-{os.getpid()}"
    probe.write_text("", encoding="utf-8")
    probe.unlink()


class Runtime:
    def __init__(self, data_dir, review_dir, extract_fn, header_fn,
                 min_free_mb: int = DEFAULT_MIN_FREE_MB,
                 retention_days: int = DEFAULT_RETENTION_DAYS):
        # Checked first, before anything is opened or created. A negative
        # reserve would let uploads overdraw the disk -- silently worse than
        # no reserve at all.
        if min_free_mb < 0:
            raise ValueError("the free-disk reserve cannot be negative")
        if retention_days < 0:
            raise ValueError("the retention window cannot be negative")
        self.data = DataDir(data_dir)
        # The review dir is checked BEFORE the index is opened, so a boot
        # refused for an unwritable journal leaves no connection behind.
        self.store = ReviewStore(Path(review_dir))
        _probe_writable(self.store.root)
        self.conn = connect(self.data.db_path)
        try:
            migrate(self.conn)
        except BaseException:
            self.conn.close()
            raise
        self.repo = Repo(self.conn)
        self.uploads = Uploads(self.repo, self.data, min_free_bytes=min_free_mb * MIB)
        # Built before the Worker so the startup hook below can reach it --
        # the disk guard it shares with imports (self.uploads) must already
        # exist too.
        self.exports = Exports(self.repo, self.data, self.store, self.uploads)
        # A second always-on thread (decision 7), unrelated to the GPU
        # queue -- started only once Worker.start() below has won the
        # single-process lock (see start()).
        self.housekeeping = Housekeeping(self.repo, self.data, self.exports,
                                         retention_days=retention_days)
        # Recovery runs inside Worker.start(), under the single-process lock
        # (review C1): a second process that fails to get the lock never
        # sweeps the first one's in-flight imports.
        self.worker = Worker(self.repo, self.data, self.store, extract_fn, header_fn,
                             startup=self._run_startup_recovery)

    def _run_startup_recovery(self) -> None:
        """All three sweeps are guarded INDEPENDENTLY (CLAUDE.md §4): they
        protect unrelated failure modes (an interrupted import, an
        interrupted re-extract, an interrupted mass export), so a fault in
        one must not skip the others. Worker.start() itself wraps the whole
        call in a try/except too, but only as a backstop against something
        none of the three anticipated -- the real isolation is here."""
        try:
            self.uploads.recover()
        except Exception as e:
            log.warning("startup recovery (uploads) failed: %s", type(e).__name__)
        try:
            self._recover_reextract_stashes()
        except Exception as e:
            log.warning("startup recovery (reextract stash) failed: %s", type(e).__name__)
        try:
            self.exports.fail_interrupted()
        except Exception as e:
            log.warning("startup recovery (exports) failed: %s", type(e).__name__)

    def _recover_reextract_stashes(self) -> None:
        """A crash between app.main's reextract route renaming
        `result.json` -> `result.reextract.json` and `repo.reextract`'s
        commit (batch design §2 queue controls) can leave a stash on disk
        that disagrees with what the row says:

        * the commit never landed (the row is still `ready`/`in_review`/
          `finished`) and `result.json` is missing -- put the stash straight
          back, exactly as app.main's own failure path would have, so the
          drawing is reviewable again;
        * the commit DID land (the row is `queued`/`extracting`/`failed`/
          `cancelled`) or the row is gone entirely -- the discard already
          completed before the crash, and the stash is simply an orphan of
          that discard. Deleted.

        Never touches a row whose `result.json` is already back in place:
        that means the drawing was retried or re-extracted again since the
        crash, and the stash is stale leftover from the earlier attempt, not
        this one's to restore."""
        if not self.data.docs.is_dir():
            return
        for d in self.data.docs.iterdir():
            if not d.is_dir():
                continue
            stash = d / "result.reextract.json"
            if not stash.is_file():
                continue
            try:
                row = self.repo.get(d.name)
            except UnknownDocument:
                stash.unlink(missing_ok=True)
                continue
            result_path = d / "result.json"
            if row["state"] in ("ready", "in_review", "finished") and not result_path.is_file():
                stash.rename(result_path)
            else:
                stash.unlink(missing_ok=True)

    @classmethod
    def from_env(cls, extract_fn, header_fn) -> "Runtime":
        data = Path(os.environ.get("SINDRI_DATA_DIR") or "/data")
        reviews = Path(os.environ.get("SINDRI_REVIEW_DIR") or data / "reviews")
        # A malformed or negative value fails the boot loudly rather than
        # running with no reserve. "0" turns the guard off (the test suite
        # does).
        min_free = int(os.environ.get("SINDRI_MIN_FREE_MB") or DEFAULT_MIN_FREE_MB)
        retention_days = int(os.environ.get("SINDRI_RETENTION_DAYS") or DEFAULT_RETENTION_DAYS)
        return cls(data, reviews, extract_fn, header_fn,
                  min_free_mb=min_free, retention_days=retention_days)

    def start(self) -> None:
        self.worker.start()
        # Only after the worker holds its single-process lock: a second
        # uvicorn worker that failed to get it never starts housekeeping
        # either, so two processes can never purge the same drawing twice.
        self.housekeeping.start()

    def close(self) -> None:
        self.housekeeping.stop()
        self.worker.stop()
        # A stop() that timed out leaves the loop finishing its drawing on
        # this connection; closing it under that thread would turn a clean
        # settle into a ProgrammingError. The process is exiting anyway.
        if not self.worker.running:
            self.conn.close()
