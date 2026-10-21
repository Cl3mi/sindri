"""SINDRI_CAMPAIGN_ONLY (plan C1 decision 10): the public instance serves the
review and nothing else. An allowlist, not a denylist: a route added later is
closed until someone decides a reviewer needs it."""
import pytest

from app.campaign.policy import allowed

D = "c" * 32


@pytest.mark.parametrize("method,path", [
    ("GET", "/"), ("GET", "/js/main.js"), ("GET", "/api/health"),
    ("GET", "/api/documents"), ("GET", f"/api/documents/{D}"), ("GET", f"/api/image/{D}"),
    ("GET", "/api/batches"),
    ("POST", "/api/auth"), ("POST", f"/api/documents/{D}/open"),
    ("POST", f"/api/session/{D}/events"), ("POST", f"/api/session/{D}/seal"),
    ("POST", "/api/read_region"),
])
def test_what_a_reviewer_needs_is_allowed(method, path):
    assert allowed(method, path)


@pytest.mark.parametrize("method,path", [
    ("GET", f"/api/documents/{D}/export/xlsx"), ("GET", "/api/exports/x/download"),
    ("POST", "/api/batches"), ("POST", "/api/uploads"), ("PUT", "/api/uploads/x"),
    ("POST", "/api/exports"), ("POST", "/api/queue/pause"),
    ("POST", f"/api/documents/{D}/reextract"), ("POST", f"/api/documents/{D}/retry"),
    ("DELETE", f"/api/documents/{D}"), ("DELETE", "/api/batches/x"),
    ("POST", f"/api/session/{D}/events/extra"), ("POST", "/api/some_future_route"),
])
def test_everything_else_is_closed(method, path):
    assert not allowed(method, path)
