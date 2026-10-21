"""SINDRI_CAMPAIGN_ONLY (plan C1 decision 10). Read per request, like the
access token (app/auth.py), so tests and a real deployment share one truth:
the environment the process actually runs with."""
import os
import re

_HEX = r"[0-9a-f]{32}"
# Exactly what a reviewer's browser calls. Both verbs are positive lists: a
# GET route added later (an export status, a listing of someone else's
# data) must stay closed on the public host until someone decides otherwise.
_GET_ALLOW = (re.compile(r"\A/api/health\Z"),
              re.compile(r"\A/api/documents\Z"),
              re.compile(rf"\A/api/documents/{_HEX}\Z"),
              re.compile(rf"\A/api/image/{_HEX}\Z"),
              re.compile(r"\A/api/batches\Z"),
              re.compile(rf"\A/api/batches/{_HEX}\Z"))
_POST_ALLOW = (re.compile(r"\A/api/auth\Z"),
               re.compile(rf"\A/api/documents/{_HEX}/open\Z"),
               re.compile(rf"\A/api/session/{_HEX}/(events|seal)\Z"),
               re.compile(r"\A/api/read_region\Z"))


def campaign_only() -> bool:
    return os.environ.get("SINDRI_CAMPAIGN_ONLY", "").lower() in ("1", "true", "yes")


def allowed(method: str, path: str) -> bool:
    if not path.startswith("/api/"):
        return method in ("GET", "HEAD")      # the static UI
    if method in ("GET", "HEAD"):
        return any(p.match(path) for p in _GET_ALLOW)
    if method == "POST":
        return any(p.match(path) for p in _POST_ALLOW)
    return False
