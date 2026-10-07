"""Runtime wiring (batch design §1, decision 30: the journal is mandatory)."""
import fcntl
import os

import pytest

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


def test_start_sweeps_unfinished_imports_before_the_worker_runs(tmp_path):
    rt = make(tmp_path)
    orphan = rt.data.doc_dir("a" * 32)
    orphan.mkdir()
    (orphan / ".importing").write_bytes(b"")
    try:
        rt.start()
        assert not orphan.exists()
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
    in_flight.mkdir()
    (in_flight / ".importing").write_bytes(b"")
    holder = open(rt.data.root / "worker.lock", "w")      # the other process
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(RuntimeError, match="another process"):
            rt.start()
        assert (in_flight / ".importing").exists()
    finally:
        holder.close()
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
