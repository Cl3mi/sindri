"""Doc-id anonymization: part numbers must never reach an AI context."""
import stat

import pytest

from app.eval.anon import (Anonymizer, doc_hash, ensure_salt, SALT_ENV,
                           SaltMissing, salt_is_persistent)


def test_doc_hash_is_stable_short_and_salt_dependent():
    assert doc_hash("T1025300_B", "salt-a") == doc_hash("T1025300_B", "salt-a")
    assert doc_hash("T1025300_B", "salt-a") != doc_hash("T1025300_B", "salt-b")
    assert doc_hash("T1025300_B", "s") != doc_hash("T1025206_D", "s")
    h = doc_hash("T1025300_B", "s")
    assert len(h) == 8 and all(c in "0123456789abcdef" for c in h)


def test_hash_does_not_leak_the_part_number():
    assert "T1025300" not in doc_hash("T1025300_B", "s")


def test_ensure_salt_creates_once_and_stays_private(tmp_path, monkeypatch):
    monkeypatch.delenv(SALT_ENV, raising=False)
    path = tmp_path / "sub" / "salt"
    first = ensure_salt(path, mint=True)
    second = ensure_salt(path)
    assert first == second and len(first) >= 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_anonymizer_hashes_by_default_and_can_be_disabled():
    assert Anonymizer("s")("T1025300_B") == doc_hash("T1025300_B", "s")
    assert Anonymizer("s", enabled=False)("T1025300_B") == "T1025300_B"


def test_anonymizer_mapping_lets_a_human_trace_back(tmp_path):
    a = Anonymizer("s")
    mapping = a.mapping(["T1", "T2"])
    assert len(mapping) == 2
    assert mapping[a("T1")] == "T1"


def test_salt_is_persistent_detects_an_absent_salt(tmp_path, monkeypatch):
    """False means the next ensure_salt() call mints a throwaway — which is
    exactly what happens inside the --rm GPU container."""
    monkeypatch.delenv(SALT_ENV, raising=False)
    assert salt_is_persistent(tmp_path / "absent") is False


def test_salt_is_persistent_detects_a_salt_file(tmp_path, monkeypatch):
    monkeypatch.delenv(SALT_ENV, raising=False)
    path = tmp_path / "salt"
    path.write_text("cafebabe", encoding="utf-8")
    assert salt_is_persistent(path) is True


def test_salt_is_persistent_honours_the_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv(SALT_ENV, "cafebabe")
    assert salt_is_persistent(tmp_path / "absent") is True


def test_a_missing_salt_is_refused_not_minted(tmp_path, monkeypatch):
    """The silent failure H3 closes: a machine that never received the salt
    file used to mint a fresh one on the first hashing command, warn at most
    once, and from then on look persistent -- every id it printed or committed
    joined to nothing already published. Minting is now a deliberate act."""
    monkeypatch.delenv(SALT_ENV, raising=False)
    monkeypatch.delenv("SINDRI_ALLOW_NEW_SALT", raising=False)
    path = tmp_path / "absent"
    with pytest.raises(SaltMissing):
        ensure_salt(path)
    assert not path.exists()
    monkeypatch.setattr("app.eval.anon.DEFAULT_SALT_FILE", path)
    with pytest.raises(SaltMissing):
        Anonymizer()
    assert not path.exists()


def test_minting_needs_an_explicit_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv(SALT_ENV, raising=False)
    monkeypatch.setenv("SINDRI_ALLOW_NEW_SALT", "1")
    path = tmp_path / "fresh"
    assert len(ensure_salt(path)) >= 32 and path.exists()


def test_an_ephemeral_anonymizer_never_touches_the_disk(tmp_path, monkeypatch):
    """The GPU container's path: ids that join to nothing, and no file left
    behind that a later run could mistake for the real salt."""
    monkeypatch.delenv(SALT_ENV, raising=False)
    monkeypatch.setattr("app.eval.anon.DEFAULT_SALT_FILE", tmp_path / "absent")
    a = Anonymizer.ephemeral()
    assert len(a("T1")) == 8 and a("T1") == a("T1")
    assert not (tmp_path / "absent").exists()
