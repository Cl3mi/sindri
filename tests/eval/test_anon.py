"""Doc-id anonymization: part numbers must never reach an AI context."""
import stat

from app.eval.anon import Anonymizer, doc_hash, ensure_salt


def test_doc_hash_is_stable_short_and_salt_dependent():
    assert doc_hash("T1025300_B", "salt-a") == doc_hash("T1025300_B", "salt-a")
    assert doc_hash("T1025300_B", "salt-a") != doc_hash("T1025300_B", "salt-b")
    assert doc_hash("T1025300_B", "s") != doc_hash("T1025206_D", "s")
    h = doc_hash("T1025300_B", "s")
    assert len(h) == 8 and all(c in "0123456789abcdef" for c in h)


def test_hash_does_not_leak_the_part_number():
    assert "T1025300" not in doc_hash("T1025300_B", "s")


def test_ensure_salt_creates_once_and_stays_private(tmp_path):
    path = tmp_path / "sub" / "salt"
    first = ensure_salt(path)
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
