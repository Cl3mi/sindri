"""Mass export (batch design §4): a ZIP of many drawings' files plus a
manifest, built on its own thread so it never queues behind the GPU worker,
rendered through the SAME function (app.batch.export_render.render_export)
a lone download uses."""
import csv
import io
import json
import zipfile

import pytest

from app.batch import db
from app.batch.exports import Exports
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.batch.uploads import Uploads
from app.review.store import ReviewStore


@pytest.fixture
def rig(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    repo = Repo(conn)
    data = DataDir(tmp_path / "data")
    store = ReviewStore(tmp_path / "reviews")
    # min_free_bytes=0: the disk guard is OFF by default here, the same
    # convention tests/conftest.py uses for uploads (SINDRI_MIN_FREE_MB=0) --
    # the disk-guard tests below build their OWN Uploads with a patched
    # free_bytes instead of reusing this one.
    uploads = Uploads(repo, data, min_free_bytes=0)
    return repo, data, store, Exports(repo, data, store, uploads)


def _finished_document(repo, data, store, doc_id, *, rows, batch_name="Flansch"):
    """A drawing sealed at revision 1, with the files export_render needs."""
    doc = repo.create_document(doc_id, "w1.pdf", pages=1, sha256=None, batch_name=batch_name)
    repo.enqueue(doc_id)
    repo.claim_next()
    d = data.doc_dir(doc_id)
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps(
        {"notes": None, "marks": None, "title_block": []}), encoding="utf-8")
    (d / "input.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    repo.mark_ready(doc_id)
    writer = store.create(doc_id, {}, {"rows": rows})
    store.seal(doc_id, writer, 0, rows, [])
    repo.mark_finished(doc_id, 1, 0)
    return doc["batch_id"]


def test_mass_export_builds_a_zip_with_a_manifest(rig, monkeypatch):
    repo, data, store, exports = rig
    import app.batch.export_render as export_render
    monkeypatch.setattr(export_render, "write_workbook",
                        lambda rows, out, **kw: out.write_bytes(b"XLSX"))
    monkeypatch.setattr(export_render, "render_ballooned_pdf",
                        lambda src, rows, dpi, out_path: out_path.write_bytes(b"%PDF"))
    row = {"id": "a", "pos": 1, "nominal": "20", "suggested": False}
    _finished_document(repo, data, store, "a" * 32, rows=[row])
    job = exports.start(["a" * 32])
    exports._run(job["id"], ["a" * 32])   # synchronous for the test
    status = repo.get_export(job["id"])
    assert status["state"] == "done"
    assert status["done"] == status["total"] == 1
    with zipfile.ZipFile(data.export_zip_path(job["id"])) as zf:
        names = set(zf.namelist())
        assert "manifest.csv" in names
        assert any(n.endswith(".xlsx") for n in names)
        assert any(n.endswith("_ballooned.pdf") for n in names)
        rows_out = list(csv.DictReader(io.StringIO(zf.read("manifest.csv").decode())))
    assert rows_out[0]["skip_reason"] == ""
    assert rows_out[0]["batch"] == "Flansch"
    assert rows_out[0]["revision_exported"] == "1"


def test_mass_export_skips_an_unfinished_drawing_without_failing_the_job(rig, monkeypatch):
    repo, data, store, exports = rig
    import app.batch.export_render as export_render
    monkeypatch.setattr(export_render, "write_workbook",
                        lambda rows, out, **kw: out.write_bytes(b"XLSX"))
    monkeypatch.setattr(export_render, "render_ballooned_pdf",
                        lambda src, rows, dpi, out_path: out_path.write_bytes(b"%PDF"))
    row = {"id": "a", "pos": 1, "nominal": "20", "suggested": False}
    _finished_document(repo, data, store, "a" * 32, rows=[row])
    repo.create_document("b" * 32, "w2.pdf", pages=1, sha256=None)   # left `staged`

    job = exports.start(["a" * 32, "b" * 32])
    exports._run(job["id"], ["a" * 32, "b" * 32])
    assert repo.get_export(job["id"])["state"] == "done"
    with zipfile.ZipFile(data.export_zip_path(job["id"])) as zf:
        rows_out = {r["file"] or r["skip_reason"]: r
                    for r in csv.DictReader(io.StringIO(zf.read("manifest.csv").decode()))}
    reasons = [r["skip_reason"] for r in rows_out.values()]
    assert "not_finished" in reasons
    assert sum(1 for r in reasons if r == "") == 1


def test_a_drawing_that_fails_to_render_lands_in_the_manifest_not_the_job(rig, monkeypatch):
    """One drawing's failure must not fail the whole job (batch design §5)."""
    repo, data, store, exports = rig
    import app.batch.export_render as export_render

    def broken(rows, out, **kw):
        raise TypeError("boom")
    monkeypatch.setattr(export_render, "write_workbook", broken)
    monkeypatch.setattr(export_render, "render_ballooned_pdf",
                        lambda src, rows, dpi, out_path: out_path.write_bytes(b"%PDF"))
    row = {"id": "a", "pos": 1, "nominal": "20", "suggested": False}
    _finished_document(repo, data, store, "a" * 32, rows=[row])

    job = exports.start(["a" * 32])
    exports._run(job["id"], ["a" * 32])
    assert repo.get_export(job["id"])["state"] == "done"
    with zipfile.ZipFile(data.export_zip_path(job["id"])) as zf:
        (manifest_row,) = list(csv.DictReader(io.StringIO(zf.read("manifest.csv").decode())))
    assert manifest_row["skip_reason"] == "export_error: TypeError"


def test_a_reopened_drawing_exports_its_last_sealed_revision_with_a_note(rig, monkeypatch):
    repo, data, store, exports = rig
    import app.batch.export_render as export_render
    monkeypatch.setattr(export_render, "write_workbook",
                        lambda rows, out, **kw: out.write_bytes(b"XLSX"))
    monkeypatch.setattr(export_render, "render_ballooned_pdf",
                        lambda src, rows, dpi, out_path: out_path.write_bytes(b"%PDF"))
    row = {"id": "a", "pos": 1, "nominal": "20", "suggested": False}
    _finished_document(repo, data, store, "a" * 32, rows=[row])
    repo.events_appended("a" * 32, 1)   # a new reviewer event reopens it: finished -> in_review

    job = exports.start(["a" * 32])
    exports._run(job["id"], ["a" * 32])
    with zipfile.ZipFile(data.export_zip_path(job["id"])) as zf:
        (manifest_row,) = list(csv.DictReader(io.StringIO(zf.read("manifest.csv").decode())))
    assert manifest_row["skip_reason"] == "reopened — later changes not included"
    assert manifest_row["file"]   # still exported, not skipped


def test_single_export_and_mass_export_are_byte_identical(rig, monkeypatch, tmp_path):
    """Export determinism (batch design §5): a drawing exported alone and
    the same drawing inside a mass-export ZIP must be the same bytes, because
    both call app.batch.export_render.render_export under the same cache."""
    repo, data, store, exports = rig
    import app.batch.export_render as export_render
    monkeypatch.setattr(export_render, "write_workbook",
                        lambda rows, out, **kw: out.write_bytes(b"deterministic xlsx"))
    monkeypatch.setattr(export_render, "render_ballooned_pdf",
                        lambda src, rows, dpi, out_path: out_path.write_bytes(b"%PDF deterministic"))
    row = {"id": "a", "pos": 1, "nominal": "20", "suggested": False}
    _finished_document(repo, data, store, "a" * 32, rows=[row])
    sealed = store.revision("a" * 32, 1)
    direct = export_render.render_export(
        "a" * 32, data.doc_dir("a" * 32), 1, "xlsx", sealed).read_bytes()

    job = exports.start(["a" * 32])
    exports._run(job["id"], ["a" * 32])
    with zipfile.ZipFile(data.export_zip_path(job["id"])) as zf:
        names = [n for n in zf.namelist() if n.endswith(".xlsx")]
        zipped = zf.read(names[0])
    assert zipped == direct


def test_delete_expired_zips_removes_only_old_ones(rig):
    import time
    repo, data, store, exports = rig
    job = repo.create_export(["a" * 32])
    p = data.export_zip_path(job["id"])
    p.write_bytes(b"x")
    import os
    old = time.time() - 25 * 3600
    os.utime(p, (old, old))
    fresh_job = repo.create_export(["b" * 32])
    data.export_zip_path(fresh_job["id"]).write_bytes(b"y")
    removed = exports.delete_expired_zips()
    assert removed == 1
    assert not p.is_file()
    assert data.export_zip_path(fresh_job["id"]).is_file()


def test_fail_interrupted_marks_a_running_job_failed_and_removes_its_temp_zip(rig):
    """At startup: a job still `running` was cut off by a crash or restart.
    The thread that was building it is gone -- there is nothing to resume a
    ZIP build from (unlike a GPU extraction, which keeps result.json for a
    cheap Retry) -- so it is simply marked failed, and its half-written
    temp ZIP (the exact name _run's own tempfile.mkstemp produces) is
    cleaned up rather than left behind forever in exports/."""
    repo, data, store, exports = rig
    job = repo.create_export(["a" * 32])
    stray = data.exports / f"{job['id']}.somejunk.tmp.zip"
    stray.write_bytes(b"partial")
    unrelated = data.exports / "not-a-temp-zip.zip"
    unrelated.write_bytes(b"keep me")

    exports.fail_interrupted()

    got = repo.get_export(job["id"])
    assert (got["state"], got["error_type"]) == ("failed", "interrupted")
    assert not stray.exists()
    assert unrelated.is_file()   # only THIS job's temp zip is removed


def test_fail_interrupted_leaves_a_done_job_alone(rig):
    repo, data, store, exports = rig
    job = repo.create_export(["a" * 32])
    repo.finish_export(job["id"], "done")
    exports.fail_interrupted()
    assert repo.get_export(job["id"])["state"] == "done"


def test_mass_export_fails_early_when_the_disk_reserve_is_already_breached(rig, tmp_path):
    """The disk guard (batch design §4, decision 13) is checked before any
    byte of the ZIP is written -- a multi-gigabyte export must not start at
    all if the reserve is already gone."""
    repo, data, store, _unused_exports = rig
    from app.batch.exports import Exports as ExportsClass
    from app.batch.uploads import Uploads

    # A min_free_bytes no real free-space reading can satisfy: guard_disk
    # always raises DiskFull, exactly as it does in production when the
    # reserve is breached.
    guarded_uploads = Uploads(repo, data, min_free_bytes=1,
                              free_bytes=lambda path: 0)
    exports = ExportsClass(repo, data, store, guarded_uploads)
    row = {"id": "a", "pos": 1, "nominal": "20", "suggested": False}
    _finished_document(repo, data, store, "a" * 32, rows=[row])

    job = exports.start(["a" * 32])
    exports._run(job["id"], ["a" * 32])

    got = repo.get_export(job["id"])
    assert (got["state"], got["error_type"]) == ("failed", "OutOfDisk")
    assert not data.export_zip_path(job["id"]).is_file()
    assert not list(data.exports.glob(f"{job['id']}.*.tmp.zip"))   # temp zip cleaned up


def test_mass_export_fails_between_drawings_when_the_reserve_runs_out(rig, monkeypatch):
    """Checked between drawings too, not only once at the start: a long job
    exporting many large drawings must not be allowed to fill the disk one
    drawing at a time."""
    import app.batch.export_render as export_render
    import app.batch.uploads as uploads_mod
    from app.batch.exports import Exports as ExportsClass
    from app.batch.uploads import Uploads
    repo, data, store, _unused_exports = rig

    row = {"id": "a", "pos": 1, "nominal": "20", "suggested": False}
    _finished_document(repo, data, store, "a" * 32, rows=[row])
    _finished_document(repo, data, store, "b" * 32, rows=[row])

    # Free space enough for the FIRST guard_disk() call (before the loop),
    # gone by the second (before the second drawing) -- real behaviour under
    # a disk genuinely filling up during a long export.
    calls = {"n": 0}

    def shrinking_free(path):
        calls["n"] += 1
        return 10 * uploads_mod.MIB if calls["n"] == 1 else 0

    guarded_uploads = Uploads(repo, data, min_free_bytes=1 * uploads_mod.MIB,
                              free_bytes=shrinking_free)
    exports = ExportsClass(repo, data, store, guarded_uploads)
    monkeypatch.setattr(export_render, "write_workbook",
                        lambda rows, out, **kw: out.write_bytes(b"XLSX"))
    monkeypatch.setattr(export_render, "render_ballooned_pdf",
                        lambda src, rows, dpi, out_path: out_path.write_bytes(b"%PDF"))

    job = exports.start(["a" * 32, "b" * 32])
    exports._run(job["id"], ["a" * 32, "b" * 32])

    got = repo.get_export(job["id"])
    assert (got["state"], got["error_type"]) == ("failed", "OutOfDisk")
    assert not data.export_zip_path(job["id"]).is_file()
