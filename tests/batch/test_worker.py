"""The background extraction worker (batch design §1, §5). Driven through
run_once() so every test is deterministic; start()/stop() are tested only for
the single-worker lock and the startup requeue."""
import json
import shutil

import pytest

from app.batch import db
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.batch.worker import Cancelled, Worker
from app.models import Characteristic, ExtractionResult
from app.review.store import ReviewStore

A = "a" * 32
B = "b" * 32


class Harness:
    def __init__(self, tmp_path):
        self.data = DataDir(tmp_path / "data")
        conn = db.connect(self.data.db_path)
        db.migrate(conn)
        self.repo = Repo(conn)
        self.store = ReviewStore(tmp_path / "reviews")
        self.extract_calls = 0
        self.on_extract = lambda progress: None
        self.header = lambda pdf: {"h": 1}
        self.worker = Worker(self.repo, self.data, self.store,
                             self._extract, lambda pdf: self.header(pdf))

    def _extract(self, pdf_path, work_dir, progress):
        self.extract_calls += 1
        progress("render")
        self.on_extract(progress)
        progress("place")
        return ExtractionResult(characteristics=[Characteristic(pos=1, id="r1", nominal="20")])

    def queue(self, doc_id=A):
        d = self.data.doc_dir(doc_id)
        d.mkdir(parents=True)
        (d / "input.pdf").write_bytes(b"%PDF-1.4")
        self.repo.create_document(doc_id, "a.pdf", 1, "0" * 64)
        self.repo.enqueue(doc_id)

    def remove(self, doc_id=A):
        """What Cancel then Remove does from the UI: the row and the drawing's
        directory go while the run may still be physically going."""
        self.repo.mark_cancelled(doc_id)
        assert self.repo.delete(doc_id)
        shutil.rmtree(self.data.doc_dir(doc_id))


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def test_a_queued_drawing_is_extracted_and_becomes_reviewable(h):
    h.queue()
    assert h.worker.run_once() == A
    assert h.repo.get(A)["state"] == "ready"
    result = json.loads((h.data.doc_dir(A) / "result.json").read_text())
    assert [r["id"] for r in result["rows"]] == ["r1"]
    proposal = json.loads((h.store.root / A / "proposal.json").read_text())
    assert proposal["rows"] == result["rows"]


def test_an_empty_queue_runs_nothing(h):
    assert h.worker.run_once() is None


def test_progress_is_visible_while_running_and_cleared_after(h):
    seen = {}
    h.on_extract = lambda progress: seen.update(h.worker.progress.get(A) or {})
    h.queue()
    h.worker.run_once()
    assert seen["step"] == "render"
    assert A not in h.worker.progress


def test_the_current_job_is_exposed_while_it_runs_and_cleared_after(h):
    """Task 5's delete endpoint refuses the worker's current job by this."""
    seen = []
    h.on_extract = lambda progress: seen.append(h.worker.current)
    assert h.worker.current is None
    h.queue()
    h.worker.run_once()
    assert seen == [A]
    assert h.worker.current is None


def test_an_extraction_exception_fails_the_drawing_by_type_only(h):
    def boom(progress):
        raise RuntimeError("client text that must never be stored")
    h.on_extract = boom
    h.queue()
    h.worker.run_once()
    d = h.repo.get(A)
    assert (d["state"], d["error_type"]) == ("failed", "RuntimeError")
    assert not (h.store.root / A).exists()


def test_a_cancel_stops_the_run_at_the_next_progress_report(h):
    def cancel_then_continue(progress):
        h.repo.mark_cancelled(A)     # what POST /cancel does while it runs
        progress("detect")
    h.on_extract = cancel_then_continue
    h.queue()
    h.worker.run_once()
    assert h.repo.get(A)["state"] == "cancelled"
    assert not (h.data.doc_dir(A) / "result.json").exists()


def test_a_cancel_that_arrives_after_the_last_report_still_wins(h):
    h._extract_orig = h._extract

    def finish_then_cancel(pdf_path, work_dir, progress):
        r = h._extract_orig(pdf_path, work_dir, progress)
        h.repo.mark_cancelled(A)
        return r
    h.worker.extract_fn = finish_then_cancel
    h.queue()
    h.worker.run_once()
    assert h.repo.get(A)["state"] == "cancelled"
    # The user's cancel stands, so no review record is created for a drawing
    # the list shows as cancelled.
    assert not (h.store.root / A).exists()


def test_a_drawing_removed_mid_run_stops_it_and_leaves_nothing_behind(h):
    """Cancel is cooperative, so Cancel then Remove can land while extract()
    is still running. The next progress report must unwind the run without
    crashing the loop, and nothing of the drawing may be written back."""
    def remove_then_continue(progress):
        h.remove(A)
        progress("detect")
    h.on_extract = remove_then_continue
    h.queue()
    assert h.worker.run_once() == A
    assert not h.data.doc_dir(A).exists()
    assert not (h.store.root / A).exists()
    assert h.worker.current is None and A not in h.worker.progress
    # The worker carries on with the next drawing.
    h.on_extract = lambda progress: None
    h.queue(B)
    assert h.worker.run_once() == B
    assert h.repo.get(B)["state"] == "ready"


def test_a_drawing_removed_after_the_last_report_is_not_written_back(h):
    h._extract_orig = h._extract

    def finish_then_remove(pdf_path, work_dir, progress):
        r = h._extract_orig(pdf_path, work_dir, progress)
        h.remove(A)
        return r
    h.worker.extract_fn = finish_then_remove
    h.queue()
    assert h.worker.run_once() == A
    assert not h.data.doc_dir(A).exists()
    assert not (h.store.root / A).exists()
    assert h.worker.current is None


def test_a_review_store_fault_keeps_the_extraction_and_retry_skips_the_gpu(h):
    """result.json is written first, so a drawing whose review record could
    not be created is retried from it -- not re-extracted for ~15 GPU minutes."""
    def bad_header(pdf):
        raise OSError("disk")
    h.header = bad_header
    h.queue()
    h.worker.run_once()
    d = h.repo.get(A)
    assert (d["state"], d["error_type"]) == ("failed", "review_store:OSError")
    assert (h.data.doc_dir(A) / "result.json").is_file()
    h.header = lambda pdf: {"h": 1}
    h.repo.retry(A)
    h.worker.run_once()
    assert h.repo.get(A)["state"] == "ready"
    assert h.extract_calls == 1


def test_a_second_worker_on_the_same_data_dir_refuses_to_start(h):
    other = Worker(h.repo, h.data, h.store, h._extract, lambda pdf: {})
    h.worker.start()
    try:
        with pytest.raises(RuntimeError, match="single worker"):
            other.start()
    finally:
        h.worker.stop()


def test_start_requeues_a_run_cut_off_by_a_restart(h):
    h.queue()
    h.repo.claim_next()          # extracting when the process died
    h.repo.set_paused(True)      # keep the loop from running it during the test
    h.worker.start()
    try:
        assert h.repo.get(A)["state"] == "queued"
    finally:
        h.worker.stop()


def test_cancelled_is_an_exception_the_pipeline_can_raise_through():
    assert issubclass(Cancelled, Exception)
