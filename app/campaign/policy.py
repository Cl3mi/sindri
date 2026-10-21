"""SINDRI_CAMPAIGN_ONLY (plan C1 decision 10). Read per request, like the
access token (app/auth.py), so tests and a real deployment share one truth:
the environment the process actually runs with."""
import os
import re

_HEX = r"[0-9a-f]{32}"
# Exactly what a reviewer's browser calls. Exports are GETs, so GET is not
# blanket-allowed under /api/: the two download shapes are refused first.
_GET_DENY = (re.compile(rf"\A/api/documents/{_HEX}/export/[a-z]+\Z"),
             re.compile(r"\A/api/exports(/.*)?\Z"))
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
        return not any(p.match(path) for p in _GET_DENY)
    if method == "POST":
        return any(p.match(path) for p in _POST_ALLOW)
    return False
