"""Runtime wiring (batch design §1, decision 30: the journal is mandatory)."""
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
