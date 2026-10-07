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
