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


@pytest.mark.parametrize("method,path", [
    ("HEAD", f"/api/documents/{D}/export/xlsx"), ("GET", f"/api/exports/{D}"),
    ("POST", f"/api/documents/{D}/open/"), ("GET", "/api/some_future_route"),
    ("GET", f"/api/batches/{D}/extra"),
])
def test_near_misses_are_closed(method, path):
    assert not allowed(method, path)


@pytest.mark.parametrize("method,path", [
    ("GET", "/docs"), ("HEAD", "/docs"), ("GET", "/docs/"),
    ("GET", "/docs/oauth2-redirect"), ("GET", "/redoc"), ("GET", "/openapi.json"),
    ("HEAD", "/openapi.json"),
])
def test_the_api_schema_and_its_viewers_are_closed(method, path):
    """FastAPI serves its route schema and two viewers OUTSIDE /api, where
    the static-UI rule would admit them: on the public host that is a map
    of every closed route, and /docs sits in front of no access token.
    Nothing a reviewer runs needs them; a local instance keeps them."""
    assert not allowed(method, path)


def test_every_registered_route_is_classified():
    """Walks the real route table, so a NEW /api route fails here until
    someone decides whether a reviewer on the public host needs it (and adds
    it to the policy and to this set)."""
    import re
    from fastapi.routing import APIRoute
    from app.main import app

    admitted = set()
    for r in app.routes:
        if not isinstance(r, APIRoute) or not r.path.startswith("/api/"):
            continue
        concrete = re.sub(r"\{kind\}", "xlsx", r.path)
        concrete = re.sub(r"\{[^}]+\}", D, concrete)
        for m in r.methods:
            if allowed(m, concrete):
                admitted.add((m, r.path))
    assert admitted == {
        ("POST", "/api/auth"),
        ("GET", "/api/health"),
        ("GET", "/api/batches"),
        ("GET", "/api/batches/{batch_id}"),
        ("GET", "/api/documents"),
        ("GET", "/api/documents/{doc_id}"),
        ("GET", "/api/image/{session_id}"),
        ("POST", "/api/documents/{doc_id}/open"),
        ("POST", "/api/session/{session_id}/events"),
        ("POST", "/api/session/{session_id}/seal"),
        ("POST", "/api/read_region"),
    }
