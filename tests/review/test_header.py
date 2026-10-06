"""The header says under which configuration a session was proposed, so a
grade is never pooled across policies without anyone knowing (the r3/r4/r5
lesson: compare only like with like)."""
import hashlib

from app.pipeline import policy_rules as pr
from app.pipeline import review as pipeline_review
from app.review.header import REVIEW_SCHEMA_VERSION, build_header


def test_header_records_drawing_and_pipeline_config(sample_pdf, monkeypatch):
    monkeypatch.setenv("SINDRI_VERSION", "abc123")
    h = build_header(sample_pdf, consent=True)
    assert h["review_schema_version"] == REVIEW_SCHEMA_VERSION == 1
    assert h["app_version"] == "abc123"
    assert h["drawing_sha256"] == hashlib.sha256(sample_pdf.read_bytes()).hexdigest()
    assert h["pages"] >= 1
    assert h["consent"] is True
    assert h["pipeline"]["flag_rules"] == list(pr.ACTIVE_FLAG_RULES)
    assert h["pipeline"]["drop_stages"] == [list(s) for s in pr.ACTIVE_DROP_STAGES]
    assert h["pipeline"]["low_conf"] == pipeline_review.LOW_CONF
    assert "crop_knobs" in h["pipeline"]
    assert h["created_at"].endswith("+00:00")


def test_sampling_is_none_not_a_rate_of_zero(sample_pdf):
    # Phase 1 has no audit design. None says so; 0.0 would claim a design
    # that sampled nothing, and a grade would read "0 audited" as a result.
    assert build_header(sample_pdf, consent=False)["sampling"] is None


def test_app_version_defaults_to_unknown(sample_pdf, monkeypatch):
    monkeypatch.delenv("SINDRI_VERSION", raising=False)
    assert build_header(sample_pdf, consent=False)["app_version"] == "unknown"
