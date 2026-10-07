"""Wires a deployment together (batch design §1): data dir, index, review
store and the extraction worker."""
import os
from pathlib import Path

from app.batch.db import connect, migrate
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.batch.worker import Worker
from app.review.store import ReviewStore


def _probe_writable(root: Path) -> None:
    # mkdir on an existing read-only mount succeeds silently; only a real
    # write proves the journal can be kept -- and it is mandatory now
    # (decision 30), so failing here fails the boot, loudly.
    probe = root / f".write-probe-{os.getpid()}"
    probe.write_text("", encoding="utf-8")
    probe.unlink()


class Runtime:
    def __init__(self, data_dir, review_dir, extract_fn, header_fn):
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
        self.worker = Worker(self.repo, self.data, self.store, extract_fn, header_fn)

    @classmethod
    def from_env(cls, extract_fn, header_fn) -> "Runtime":
        data = Path(os.environ.get("SINDRI_DATA_DIR") or "/data")
        reviews = Path(os.environ.get("SINDRI_REVIEW_DIR") or data / "reviews")
        return cls(data, reviews, extract_fn, header_fn)

    def start(self) -> None:
        self.worker.start()

    def close(self) -> None:
        self.worker.stop()
        # A stop() that timed out leaves the loop finishing its drawing on
        # this connection; closing it under that thread would turn a clean
        # settle into a ProgrammingError. The process is exiting anyway.
        if not self.worker.running:
            self.conn.close()
