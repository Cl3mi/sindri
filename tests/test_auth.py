"""Optional shared access token (batch design §1, decision 23). Unset
SINDRI_ACCESS_TOKEN -> open, as today. Set -> every /api/* route except the
login route and /api/health requires it, by header or by the cookie a
successful login sets once."""
from app import auth


def test_configured_token_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("SINDRI_ACCESS_TOKEN", raising=False)
    assert auth.configured_token() is None


def test_configured_token_is_none_for_an_empty_string(monkeypatch):
    """An accidentally-empty env var must not turn into "any empty string
    authenticates" -- that would be a silent, token-shaped open door."""
    monkeypatch.setenv("SINDRI_ACCESS_TOKEN", "")
    assert auth.configured_token() is None


def test_configured_token_is_none_for_whitespace_only(monkeypatch):
    """A whitespace-only value is a config accident (a stray newline from a
    secrets file, an unset-but-quoted env var), not a secret anyone chose --
    treat it the same as unset rather than accepting " " as the token."""
    monkeypatch.setenv("SINDRI_ACCESS_TOKEN", "   \t  ")
    assert auth.configured_token() is None


def test_configured_token_reads_the_env_var(monkeypatch):
    monkeypatch.setenv("SINDRI_ACCESS_TOKEN", "secret123")
    assert auth.configured_token() == "secret123"


def test_configured_token_preserves_internal_whitespace(monkeypatch):
    """Only a value that is ENTIRELY whitespace is rejected -- a real token
    that happens to contain a space is still a value someone chose."""
    monkeypatch.setenv("SINDRI_ACCESS_TOKEN", "a b")
    assert auth.configured_token() == "a b"


def test_token_matches_is_true_only_for_the_exact_value():
    assert auth.token_matches("a", "a") is True
    assert auth.token_matches("a", "b") is False
    assert auth.token_matches("a", "ab") is False


def test_token_matches_refuses_a_non_string():
    assert auth.token_matches(None, "a") is False
    assert auth.token_matches(123, "a") is False


def test_token_matches_handles_non_ascii_without_raising():
    """hmac.compare_digest on raw str objects raises TypeError the moment
    either side holds a non-ASCII character. A presented credential is
    attacker-controlled, so the compare must never raise on it -- hashing
    both sides to bytes first (sha256) sidesteps the restriction entirely."""
    assert auth.token_matches("paßwört", "paßwört") is True
    assert auth.token_matches("paßwört", "other") is False
    assert auth.token_matches("paßwört", "paßwörty") is False


def test_requires_token_gates_api_routes_but_not_the_login_route_or_health():
    assert auth.requires_token("/api/documents") is True
    assert auth.requires_token(auth.LOGIN_PATH) is False
    assert auth.requires_token("/api/health") is False
    assert auth.requires_token("/") is False
    assert auth.requires_token("/static/main.js") is False


class _FakeRequest:
    def __init__(self, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}


def test_extract_token_prefers_the_header_over_the_cookie():
    req = _FakeRequest(headers={auth.TOKEN_HEADER: "h"}, cookies={auth.TOKEN_COOKIE: "c"})
    assert auth.extract_token(req) == "h"


def test_extract_token_falls_back_to_the_cookie():
    req = _FakeRequest(cookies={auth.TOKEN_COOKIE: "c"})
    assert auth.extract_token(req) == "c"


def test_extract_token_is_none_with_neither():
    assert auth.extract_token(_FakeRequest()) is None


def test_extract_token_tolerates_a_non_string_header_value():
    """Header parsing must never raise on something malformed -- it must
    refuse (fall through) instead."""
    req = _FakeRequest(headers={auth.TOKEN_HEADER: ""})
    assert auth.extract_token(req) is None
