"""The single export path B1 built in app/main.py, moved here unchanged so
B4's mass export (app/batch/exports.py) and a lone download call the exact
same function -- one cache, one lock, one set of bytes, not two renderers
that happen to agree today (batch design §4's export-determinism
requirement)."""
import app.main as main
from app.batch.export_render import InputUnavailable, render_export


def test_main_s_render_export_is_this_module_s_render_export():
    """The alias IS the function, not a wrapper around it: a test or a
    caller that monkeypatches one sees it from the other."""
    assert main._render_export is render_export


def test_a_missing_document_directory_raises_input_unavailable(tmp_path):
    sealed = {"rows": [], "sealed_at": "2026-10-08T00:00:00+00:00"}
    try:
        render_export("x" * 32, tmp_path / "gone", 1, "xlsx", sealed)
        assert False, "expected InputUnavailable"
    except InputUnavailable:
        pass
