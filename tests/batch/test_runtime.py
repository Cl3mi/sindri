"""Runtime wiring (batch design §1, decision 30: the journal is mandatory)."""
import fcntl
import os

import pytest

from app.batch import uploads as uploads_mod
from app.batch.runtime import Runtime


def make(tmp_path, **kw):
    return Runtime(kw.get("data", tmp_path / "data"), kw.get("reviews", tmp_path / "reviews"),
                   extract_fn=lambda *a: None, header_fn=lambda pdf: {})


def test_from_env_keeps_reviews_under_the_data_dir_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("SINDRI_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.delenv("SINDRI_REVIEW_DIR", raising=False)
    rt = Runtime.from_env(lambda *a: None, lambda pdf: {})
    try:
        assert rt.store.root == tmp_path / "d" / "reviews"
        assert rt.data.db_path.is_file()
    finally:
        rt.close()


def test_an_unwritable_review_dir_fails_loudly(tmp_path):
    """The journal is product state now: a deployment that cannot write it
    must fail at boot, not silently review drawings nobody can resume."""
    if os.geteuid() == 0:
        pytest.skip("root bypasses permission bits")
    d = tmp_path / "reviews"
    d.mkdir()
    os.chmod(d, 0o500)
    try:
        with pytest.raises(OSError):
            make(tmp_path, reviews=d)
    finally:
        os.chmod(d, 0o700)


@pytest.mark.parametrize("raw,mb", [(None, 2048), ("0", 0), ("5", 5)])
def test_the_disk_reserve_comes_from_the_environment(tmp_path, monkeypatch, raw, mb):
    monkeypatch.setenv("SINDRI_DATA_DIR", str(tmp_path / "d"))
    if raw is None:
        monkeypatch.delenv("SINDRI_MIN_FREE_MB", raising=False)
    else:
        monkeypatch.setenv("SINDRI_MIN_FREE_MB", raw)
    rt = Runtime.from_env(lambda *a: None, lambda pdf: {})
    try:
        assert rt.uploads.min_free_bytes == mb * 1024 * 1024
    finally:
        rt.close()


@pytest.mark.parametrize("raw", ["-1", "abc"])
def test_a_malformed_or_negative_reserve_fails_the_boot(tmp_path, monkeypatch, raw):
    """A negative reserve would let uploads overdraw the disk -- worse than
    no reserve, and silent. Refused like a malformed one."""
    monkeypatch.setenv("SINDRI_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SINDRI_MIN_FREE_MB", raw)
    with pytest.raises(ValueError):
        Runtime.from_env(lambda *a: None, lambda pdf: {})


def test_a_negative_reserve_is_refused_on_direct_construction_too(tmp_path):
    with pytest.raises(ValueError):
        Runtime(tmp_path / "data", tmp_path / "reviews", extract_fn=lambda *a: None,
                header_fn=lambda pdf: {}, min_free_mb=-1)
    # Refused before the index is opened: nothing is left behind to close.
    assert not (tmp_path / "data").exists()


def leftover(d):
    """A marked dir as a crash before this process left it."""
    d.mkdir()
    m = d / ".importing"
    m.write_bytes(b"")
    past = uploads_mod.PROCESS_STARTED - 3600
    os.utime(m, (past, past))


def test_start_sweeps_unfinished_imports_before_the_worker_runs(tmp_path):
    rt = make(tmp_path)
    orphan = rt.data.doc_dir("a" * 32)
    leftover(orphan)
    try:
        rt.start()
        assert not orphan.exists()
    finally:
        rt.close()


def test_recovery_runs_once_per_worker_however_often_it_restarts(tmp_path, monkeypatch):
    """A restart after stop() happens while requests are served: whatever is
    marked then is live work, not a crash's leftover."""
    rt = make(tmp_path)
    calls = []
    monkeypatch.setattr(rt.uploads, "recover", lambda: calls.append(1))
    try:
        rt.start()
        rt.worker.stop()
        rt.start()
        assert calls == [1] and rt.worker.running
    finally:
        rt.close()


def test_a_failed_recovery_does_not_keep_the_worker_from_starting(tmp_path, monkeypatch, caplog):
    """Recovery only removes leftovers; a fault in it must not hold up every
    queued drawing. Logged by TYPE: an OSError's text names a path."""
    rt = make(tmp_path)

    def boom():
        raise OSError("disk /secret/Kunde")
    monkeypatch.setattr(rt.uploads, "recover", boom)
    try:
        rt.start()
        assert rt.worker.running
    finally:
        rt.close()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "OSError" in text and "Kunde" not in text


def test_recovery_runs_only_under_the_single_worker_lock(tmp_path):
    """A second process that cannot take the lock must not sweep: the first
    process's in-flight imports are marked exactly like crash leftovers, and
    sweeping them would lose a user's file mid-import."""
    rt = make(tmp_path)
    in_flight = rt.data.doc_dir("a" * 32)
    # Dated like a crash leftover: the other process may have started before
    # this one, so only the lock -- not the marker's age -- can protect it.
    leftover(in_flight)
    holder = open(rt.data.root / "worker.lock", "w")      # the other process
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(RuntimeError, match="another process"):
            rt.start()
        assert (in_flight / ".importing").exists()
    finally:
        holder.close()
        rt.close()


def _make_ready(repo, data, doc_id):
    """A `ready` drawing with no actual extraction -- enough for the
    reextract-stash recovery sweep, which only reads repo state and the
    docs dir's files, never the review store."""
    d = data.doc_dir(doc_id)
    d.mkdir(parents=True)
    repo.create_document(doc_id, "a.drawing", 1, "0" * 64)
    repo.enqueue(doc_id)
    repo.claim_next()
    repo.mark_ready(doc_id)
    return d


def test_reextract_stash_recovery_restores_an_untouched_ready_drawing(tmp_path):
    """Crash between app.main's rename and repo.reextract's commit: the row
    is still `ready` and result.json never came back -- the stash must be
    restored so the drawing is reviewable again."""
    rt = make(tmp_path)
    doc_id = "a" * 32
    d = _make_ready(rt.repo, rt.data, doc_id)
    (d / "result.reextract.json").write_text("{}", encoding="utf-8")
    try:
        rt._recover_reextract_stashes()
        assert (d / "result.json").is_file()
        assert not (d / "result.reextract.json").exists()
    finally:
        rt.close()


def test_reextract_stash_recovery_deletes_a_stash_once_already_committed(tmp_path):
    """Crash AFTER repo.reextract's commit, before app.main deleted the
    stash: the row is `queued` -- the discard already completed, and the
    stash is just leftover to clean up, not something to restore."""
    rt = make(tmp_path)
    doc_id = "a" * 32
    d = _make_ready(rt.repo, rt.data, doc_id)
    rt.repo.reextract(doc_id)
    (d / "result.reextract.json").write_text("{}", encoding="utf-8")
    try:
        rt._recover_reextract_stashes()
        assert not (d / "result.reextract.json").exists()
        assert not (d / "result.json").exists()
    finally:
        rt.close()


def test_reextract_stash_recovery_never_overwrites_a_result_already_back(tmp_path):
    """The drawing was retried or re-extracted again since the crash:
    result.json is already there, so the stash is stale leftover from the
    earlier attempt and must not clobber the newer file."""
    rt = make(tmp_path)
    doc_id = "a" * 32
    d = _make_ready(rt.repo, rt.data, doc_id)
    (d / "result.json").write_text('{"fresh": true}', encoding="utf-8")
    (d / "result.reextract.json").write_text('{"stale": true}', encoding="utf-8")
    try:
        rt._recover_reextract_stashes()
        assert not (d / "result.reextract.json").exists()
        assert (d / "result.json").read_text(encoding="utf-8") == '{"fresh": true}'
    finally:
        rt.close()


def test_reextract_stash_recovery_deletes_an_orphan_for_a_removed_row(tmp_path):
    """The document row itself is gone (removed mid-crash): nothing to
    restore it to, so the stash is just swept."""
    rt = make(tmp_path)
    doc_id = "a" * 32
    d = rt.data.doc_dir(doc_id)
    d.mkdir(parents=True)
    (d / "result.reextract.json").write_text("{}", encoding="utf-8")
    try:
        rt._recover_reextract_stashes()
        assert not (d / "result.reextract.json").exists()
    finally:
        rt.close()


def test_reextract_stash_recovery_runs_independently_of_upload_recovery(tmp_path, monkeypatch):
    """REQUIRED: each sweep is guarded separately -- a fault in the upload
    sweep must not skip the reextract-stash sweep."""
    rt = make(tmp_path)
    doc_id = "a" * 32
    d = _make_ready(rt.repo, rt.data, doc_id)
    (d / "result.reextract.json").write_text("{}", encoding="utf-8")

    def boom():
        raise OSError("disk fault")
    monkeypatch.setattr(rt.uploads, "recover", boom)
    try:
        rt.start()
        assert rt.worker.running
        assert (d / "result.json").is_file()
        assert not (d / "result.reextract.json").exists()
    finally:
        rt.close()


def test_recovery_runs_before_interrupted_extractions_are_requeued(tmp_path, monkeypatch):
    """Inside the locked section, first: the order the plan registers."""
    rt = make(tmp_path)
    calls = []
    monkeypatch.setattr(rt.uploads, "recover", lambda: calls.append("recover"))
    real = rt.repo.requeue_interrupted

    def requeue(*a, **k):
        calls.append("requeue")
        return real(*a, **k)
    monkeypatch.setattr(rt.repo, "requeue_interrupted", requeue)
    try:
        rt.start()
    finally:
        rt.close()
    assert calls == ["recover", "requeue"]
