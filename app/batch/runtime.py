"""Wires a deployment together (batch design §1): data dir, index, review
store and the extraction worker."""
import os
from pathlib import Path

from app.batch.db import connect, migrate
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.batch.uploads import MIB, Uploads
from app.batch.worker import Worker
from app.review.store import ReviewStore

# The free-disk reserve (batch design §2): below it, uploads and unpacking
# answer 507 so an import can never fill the disk under a review journal.
DEFAULT_MIN_FREE_MB = 2048


def _probe_writable(root: Path) -> None:
    # mkdir on an existing read-only mount succeeds silently; only a real
    # write proves the journal can be kept -- and it is mandatory now
    # (decision 30), so failing here fails the boot, loudly.
    probe = root / f".write-probe-{os.getpid()}"
    probe.write_text("", encoding="utf-8")
    probe.unlink()


class Runtime:
    def __init__(self, data_dir, review_dir, extract_fn, header_fn,
                 min_free_mb: int = DEFAULT_MIN_FREE_MB):
        # Checked first, before anything is opened or created. A negative
        # reserve would let uploads overdraw the disk -- silently worse than
        # no reserve at all.
        if min_free_mb < 0:
            raise ValueError("the free-disk reserve cannot be negative")
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
        self.worker = Worker(self.repo, self.data, self.store, extract_fn, header_fn)

    @classmethod
    def from_env(cls, extract_fn, header_fn) -> "Runtime":
        data = Path(os.environ.get("SINDRI_DATA_DIR") or "/data")
        reviews = Path(os.environ.get("SINDRI_REVIEW_DIR") or data / "reviews")
        # A malformed or negative value fails the boot loudly rather than
        # running with no reserve. "0" turns the guard off (the test suite
        # does).
        min_free = int(os.environ.get("SINDRI_MIN_FREE_MB") or DEFAULT_MIN_FREE_MB)
        return cls(data, reviews, extract_fn, header_fn, min_free_mb=min_free)

    def start(self) -> None:
        self.worker.start()

    def close(self) -> None:
        self.worker.stop()
        # A stop() that timed out leaves the loop finishing its drawing on
        # this connection; closing it under that thread would turn a clean
        # settle into a ProgrammingError. The process is exiting anyway.
        if not self.worker.running:
            self.conn.close()
