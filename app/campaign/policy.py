"""SINDRI_CAMPAIGN_ONLY (plan C1 decision 10). Read per request, like the
access token (app/auth.py), so tests and a real deployment share one truth:
the environment the process actually runs with."""
import os
import re
import threading
import time
from collections import deque

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


# FastAPI's route schema and its two viewers live OUTSIDE /api, where the
# static-UI rule below would admit them: on the public host a map of every
# closed route, and in front of no access token. A local instance keeps them.
_SCHEMA = re.compile(r"\A/(docs|redoc|openapi\.json)(/|\Z)")


def allowed(method: str, path: str) -> bool:
    if _SCHEMA.match(path):
        return False
    if not path.startswith("/api/"):
        return method in ("GET", "HEAD")      # the static UI
    if method in ("GET", "HEAD"):
        return any(p.match(path) for p in _GET_ALLOW)
    if method == "POST":
        return any(p.match(path) for p in _POST_ALLOW)
    return False


# H4: SINDRI_CAMPAIGN_ONLY used to be a route allowlist only, so every read
# route still answered for any batch on the data volume. An instance holds
# ONE client's campaign by design; this is what makes that true rather than
# documented. The middleware checks the id in the path, the list handlers
# filter, and read_region (id in the body) checks itself.
_DOC_ID_ROUTE = re.compile(rf"\A/api/(?:documents|image|session)/({_HEX})(?:/|\Z)")
_BATCH_ID_ROUTE = re.compile(rf"\A/api/batches/({_HEX})\Z")


def path_doc_id(path: str):
    m = _DOC_ID_ROUTE.match(path)
    return m.group(1) if m else None


def path_batch_id(path: str):
    m = _BATCH_ID_ROUTE.match(path)
    return m.group(1) if m else None


# /api/read_region runs CPU OCR and basic auth is the only guard on the public
# host. A sliding window per session, in memory: one process serves it, and a
# restart forgiving a window is harmless.
_READ_WINDOW_S = 60.0
_hits: dict = {}
_hits_lock = threading.Lock()


def read_region_limit() -> int:
    try:
        n = int(os.environ.get("SINDRI_READ_REGION_PER_MIN", ""))
    except ValueError:
        return 30
    return n if n > 0 else 30


def allow_read_region(session_id: str, now: float = None) -> bool:
    now = time.monotonic() if now is None else now
    limit = read_region_limit()
    with _hits_lock:
        q = _hits.setdefault(session_id, deque())
        while q and now - q[0] >= _READ_WINDOW_S:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def reset_read_region_limit() -> None:
    with _hits_lock:
        _hits.clear()
