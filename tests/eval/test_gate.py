"""The reproduction gate. Each test pins a failure mode that has to be LOUD:
the version this replaced could be skipped by an absent file, which let a
direction run proceed as if it had been gated."""
import json

from app.eval.gate import check_reproduction, main


def _cmp(tmp_path, deltas, name="cmp.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"per_doc_deltas": deltas}), encoding="utf-8")
    return path


def test_absent_comparison_point_fails_instead_of_skipping(tmp_path):
    ok, message = check_reproduction(tmp_path / "does-not-exist.json")
    assert ok is False
    assert "no comparison point" in message


def test_all_zero_deltas_passes_and_states_the_count(tmp_path):
    ok, message = check_reproduction(_cmp(tmp_path, {"a": 0.0, "b": 0.0}))
    assert ok is True
    assert "2 per-document deltas" in message


def test_any_nonzero_delta_fails_and_names_the_drifted_documents(tmp_path):
    ok, message = check_reproduction(_cmp(tmp_path, {"a": 0.0, "b": -1.5}))
    assert ok is False
    assert "1 of 2" in message
    assert "'b': -1.5" in message


def test_empty_delta_map_fails_because_nothing_was_compared(tmp_path):
    ok, message = check_reproduction(_cmp(tmp_path, {}))
    assert ok is False
    assert "no per_doc_deltas" in message


def test_unreadable_file_fails_rather_than_raising(tmp_path):
    path = tmp_path / "truncated.json"
    path.write_text('{"per_doc_deltas": {"a": 0.0', encoding="utf-8")
    ok, message = check_reproduction(path)
    assert ok is False
    assert "unreadable" in message


def test_main_exits_nonzero_on_failure(tmp_path):
    assert main([str(tmp_path / "missing.json")]) == 1
    assert main([str(_cmp(tmp_path, {"a": 0.0}))]) == 0
