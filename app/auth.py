"""Optional shared access token for a deployment reachable beyond one desk
(batch design §1, decision 23). Deliberately dependency-free and separate
from app/batch and app/review -- it is not part of either's product
boundary, and nothing here ever imports app.eval."""
import hashlib
import hmac
import os

TOKEN_COOKIE = "sindri_token"
TOKEN_HEADER = "x-sindri-token"
LOGIN_PATH = "/api/auth"
# Operational monitoring must behave identically whether or not a token is
# configured, and its body already carries only counts and type names
# (CLAUDE.md §1) -- publishing it needs no secret (decision 9).
HEALTH_PATH = "/api/health"


def configured_token():
    """The deployment's secret, or None if the feature is off. Read fresh
    on every call, never cached: tests set/unset the env var per case, and a
    cached value from import time would carry over between them. An EMPTY
    or whitespace-only string reads as None too -- an accidentally-blank
    env var (or one with a stray newline from a secrets file) must not turn
    into "any empty string authenticates", and a value with no non-space
    character was never a secret anyone chose."""
    t = os.environ.get("SINDRI_ACCESS_TOKEN")
    if t is None or t.strip() == "":
        return None
    return t


def _digest(value: str) -> bytes:
    """Hash to a fixed-length digest before comparing. compare_digest on
    unequal-length bytes already returns False in constant time with
    respect to the shared prefix, but it still leaks the two lengths
    differing; hashing first removes even that, and sha256 over UTF-8 bytes
    never raises on a non-ASCII credential the way compare_digest on raw
    str objects would."""
    return hashlib.sha256(value.encode("utf-8")).digest()


def token_matches(given, expected) -> bool:
    """Constant-time, length included: a token is a secret, and a length-,
    prefix- or exception-based compare would turn a string of 401s into a
    timing oracle for guessing it one byte at a time."""
    if not isinstance(given, str) or not isinstance(expected, str):
        return False
    return hmac.compare_digest(_digest(given), _digest(expected))


def extract_token(request):
    """The header first (what a non-browser caller sends), the cookie
    otherwise (what the browser's own token prompt sets once). Tolerates a
    missing or malformed header/cookie store -- a falsy value (None, "")
    falls through to the cookie, and either lookup failing to find
    anything, never raises."""
    header = request.headers.get(TOKEN_HEADER)
    if header:
        return header
    return request.cookies.get(TOKEN_COOKIE)


def requires_token(path: str) -> bool:
    """Every /api/* route except the login route itself (how a token is
    ever supplied) and /api/health (decision 9). Static files (everything
    else) stay open regardless -- they are application code, not client
    data, and gating them would break the token prompt's own ability to
    load in the first place."""
    return path.startswith("/api/") and path not in (LOGIN_PATH, HEALTH_PATH)
