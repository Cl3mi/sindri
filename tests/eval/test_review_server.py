"""The review server, against the real handler on a synthetic corpus.

It serves client text, so the tests that matter most are the refusals: no
token, a foreign Host header, a tally path an agent could never read. The rest
check the operator's loop end to end -- page, deck, crops, autosave, finish.
"""
import json
import threading
import urllib.error
import urllib.request

import fitz
import pytest

from app.eval.review import (ReviewRefused, build_deck, load_answers,
                             load_deck, write_deck)
from app.eval.review_server import ReviewApp, make_server
from tests.eval.test_review import _roots, _rows3


@pytest.fixture
def running(tmp_path, monkeypatch):
    root = tmp_path / "client"
    for sub in ("originals", "stamped"):
        (root / sub).mkdir(parents=True)
        for doc_id in ("P1", "P2"):
            doc = fitz.open()
            doc.new_page(width=1191, height=842).insert_text((100, 100), "x")
            doc.save(root / sub / f"{doc_id}.pdf")
            doc.close()
    _roots(tmp_path, monkeypatch, root)
    deck_path = root / "reports" / "d.json"
    write_deck(deck_path, build_deck(_rows3(), "r", root / "originals",
                                     root / "stamped"))
    app = ReviewApp(deck_path, tmp_path / "docs" / "tally.json")
    srv = make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield app, base, deck_path, tmp_path
    srv.shutdown()
    srv.server_close()


def _get(url):
    return urllib.request.urlopen(url, timeout=5)


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=5)


def test_every_route_refuses_a_request_without_the_token(running):
    """Another local process, or a web page in the same browser, cannot read
    the deck without the token printed to the operator's terminal."""
    app, base, _, _ = running
    for path in ("/", "/api/deck", "/crop/g1/clean.png", "/?t=wrong"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(base + path)
        assert e.value.code == 403


def test_a_foreign_host_header_is_refused(running):
    """DNS rebinding: a page on another name pointed at 127.0.0.1."""
    app, base, _, _ = running
    req = urllib.request.Request(f"{base}/api/deck?t={app.token}",
                                 headers={"Host": "evil.example:80"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 403


def test_it_listens_on_localhost_only(running):
    app, base, _, _ = running
    assert base.startswith("http://127.0.0.1:")


def test_the_page_and_deck_are_served_with_the_token(running):
    app, base, _, _ = running
    page = _get(f"{base}/?t={app.token}")
    assert page.headers["Content-Type"].startswith("text/html")
    assert page.headers["Referrer-Policy"] == "no-referrer"
    assert page.headers["Cache-Control"] == "no-store"
    deck = json.load(_get(f"{base}/api/deck?t={app.token}"))
    assert [r["id"] for r in deck["rows"]] == ["g1", "g2", "g3"]
    assert deck["answers"] == {} and len(deck["questions"]) == 3
    assert all(r["stamped_approx"] is False for r in deck["rows"])


def test_crops_are_served_as_png_even_when_a_drawing_is_missing(running):
    app, base, deck_path, _ = running
    for which in ("clean", "stamped"):
        r = _get(f"{base}/crop/g1/{which}.png?t={app.token}")
        assert r.headers["Content-Type"] == "image/png"
        assert r.read().startswith(b"\x89PNG")
    (deck_path.parent.parent / "stamped" / "P2.pdf").unlink()
    r = _get(f"{base}/crop/g3/stamped.png?t={app.token}")
    assert r.read().startswith(b"\x89PNG")          # the placeholder


def test_an_unknown_row_is_404(running):
    app, base, _, _ = running
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(f"{base}/crop/g9/clean.png?t={app.token}")
    assert e.value.code == 404


def test_an_answer_is_persisted_and_a_bad_one_is_400(running):
    app, base, deck_path, _ = running
    assert _post(f"{base}/api/answer?t={app.token}",
                 {"id": "g1", "answer": {"drawing_shows": "position"}}
                 ).status == 200
    assert load_answers(deck_path, load_deck(deck_path))["g1"] == {
        "drawing_shows": "Position"}
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(f"{base}/api/answer?t={app.token}",
              {"id": "g1", "answer": {"drawing_shows": "Positon"}})
    assert e.value.code == 400


def test_finish_writes_the_counts_only_tally(running):
    app, base, _, tmp_path = running
    _post(f"{base}/api/answer?t={app.token}", {"id": "g1", "answer": {
        "drawing_shows": "Position", "symbol_in_transcription": "glyph",
        "gold_label_means_it": "yes", "note": "SECRET-NOTE"}})
    body = json.load(_post(f"{base}/api/finish?t={app.token}", {}))
    written = json.loads((tmp_path / "docs" / "tally.json").read_text())
    assert written == body["tally"]
    assert written["answered"] == 1 and written["freed_without_gpu"] == 1
    assert "SECRET" not in json.dumps(written)


def test_the_app_refuses_a_tally_path_inside_a_protected_root(tmp_path,
                                                             monkeypatch):
    root = tmp_path / "client"
    root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    write_deck(root / "d.json", build_deck(_rows3(), "r", "/o", "/s"))
    with pytest.raises(ReviewRefused):
        ReviewApp(root / "d.json", root / "tally.json")


def test_the_app_refuses_a_deck_outside_a_protected_root(tmp_path, monkeypatch):
    root = tmp_path / "client"
    root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    outside = tmp_path / "d.json"
    outside.write_text(json.dumps(build_deck(_rows3(), "r", "/o", "/s")))
    with pytest.raises(ReviewRefused):
        ReviewApp(outside, tmp_path / "docs" / "tally.json")


def test_the_page_is_self_contained_and_uses_only_the_served_routes(running):
    """It must work offline and send nothing anywhere, and client text must be
    set as text -- a label containing markup must never become markup."""
    app, base, _, _ = running
    html = _get(f"{base}/?t={app.token}").read().decode()
    for route in ("/api/deck", "/api/answer", "/api/finish", "/crop/"):
        assert route in html, route
    for external in ("http://", "https://", "//cdn", "<link"):
        assert external not in html, external
    assert "innerHTML" not in html
