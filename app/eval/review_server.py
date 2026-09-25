"""The operator's review server: localhost only, one session token.

Started by the OPERATOR (`runner review-serve <deck>`), never by an agent --
it serves client text, and `review-serve` is deliberately absent from the
agent guard's allowlist. It binds 127.0.0.1, checks the Host header (DNS
rebinding) and requires a random token on every request, so neither another
local process nor a web page in the same browser can read the deck. It sends
no-referrer and no-store so the token and the data stay out of history and
caches, and a CSP that forbids every external request.
"""
import hmac
import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.eval import review, review_crops

_PAGE = Path(__file__).with_name("review_page.html")
_CROP_RE = re.compile(r"^/crop/(g\d+)/(clean|stamped)\.png$")
_MAX_BODY = 64_000
_CSP = ("default-src 'none'; script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; img-src 'self'; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class ReviewApp:
    def __init__(self, deck_path, tally_out):
        self.deck_path = Path(deck_path)
        if not review.inside_protected(self.deck_path):
            raise review.ReviewRefused(
                "the deck must be inside a protected root")
        self.deck = review.load_deck(self.deck_path)
        review.load_answers(self.deck_path, self.deck)   # fail fast on mismatch
        self.tally_out = review.check_tally_out(tally_out)
        self.token = secrets.token_urlsafe(18)
        self.rows = {r["id"]: r for r in self.deck["rows"]}
        self.approx = {d: self._unmappable(d)
                       for d in {r["doc_id"] for r in self.deck["rows"]}}

    def drawing(self, which: str, doc_id: str) -> Path:
        key = "originals_dir" if which == "clean" else "stamped_dir"
        return Path(self.deck[key]) / f"{doc_id}.pdf"

    def _unmappable(self, doc_id: str) -> bool:
        """Gold positions are in the ORIGINAL's page space and the stamped crop
        inverts ingest's transform to reach the stamped sheet -- which needs
        the original's page. Without it the crop can only fall back to raw
        coordinates, and the page says so."""
        return (review_crops.page_rect(self.drawing("clean", doc_id)) is None
                and review_crops.page_rect(
                    self.drawing("stamped", doc_id)) is not None)

    def deck_payload(self):
        return {"kind": self.deck["kind"], "run": self.deck["run"],
                "questions": self.deck["questions"],
                "rows": [dict(r, stamped_approx=self.approx[r["doc_id"]])
                         for r in self.deck["rows"]],
                "answers": review.load_answers(self.deck_path, self.deck)}

    def crop(self, rid: str, which: str) -> bytes:
        row = self.rows[rid]
        path = self.drawing(which, row["doc_id"])
        page = review_crops.page_rect(path)
        if page is None:
            return review_crops.placeholder_png("drawing not available")
        clean = which == "clean"
        # The region is chosen in the ORIGINAL's space, where the deck's
        # geometry lives. The stamped sheet often has a different extent
        # (14 of 20 dev documents), so its crop is carried over by inverting
        # ingest's per-axis scale -- cutting it at original coordinates showed
        # an unrelated part of the drawing.
        orig = page if clean else review_crops.page_rect(
            self.drawing("clean", row["doc_id"]))
        rect = review_crops.crop_rect(orig or page, row["pred_box_pt"],
                                      row["gold_pt"])
        if rect is None:
            return review_crops.placeholder_png("no position recorded")
        if not clean and orig is not None:
            rect = review_crops.map_rect(rect, orig, page)
        try:
            return review_crops.render_crop(
                path, rect, row["pred_box_pt"] if clean else None,
                row["gold_pt"] if clean else None)
        except review_crops.CropError as e:
            return review_crops.placeholder_png(str(e))


def make_handler(app: ReviewApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "sindri-review"

        def log_message(self, *args):
            pass        # request lines carry the token; keep the terminal clean

        def _send(self, status: int, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", _CSP)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj):
            self._send(status, json.dumps(obj, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8")

        def _gate(self):
            port = self.server.server_address[1]
            if self.headers.get("Host", "") not in (f"127.0.0.1:{port}",
                                                    f"localhost:{port}"):
                self._json(403, {"error": "wrong host"})
                return None
            url = urlparse(self.path)
            token = parse_qs(url.query).get("t", [""])[0]
            if not hmac.compare_digest(token.encode(), app.token.encode()):
                self._json(403, {"error": "missing or wrong session token"})
                return None
            return url.path

        def do_GET(self):
            path = self._gate()
            if path is None:
                return
            if path == "/":
                self._send(200, _PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/deck":
                self._json(200, app.deck_payload())
            elif (m := _CROP_RE.match(path)) and m.group(1) in app.rows:
                self._send(200, app.crop(m.group(1), m.group(2)), "image/png")
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            path = self._gate()
            if path is None:
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                self._json(413, {"error": "request too large"})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json(400, {"error": "not JSON"})
                return
            try:
                if path == "/api/answer":
                    review.save_answer(app.deck_path, app.deck,
                                       str(body.get("id", "")),
                                       body.get("answer") or {})
                    self._json(200, {"ok": True})
                elif path == "/api/finish":
                    t = review.tally(app.deck, review.load_answers(
                        app.deck_path, app.deck))
                    review.write_tally(app.tally_out, t)
                    self._json(200, {"tally": t,
                                     "written_to": str(app.tally_out)})
                else:
                    self._json(404, {"error": "not found"})
            except review.ReviewRefused as e:
                self._json(400, {"error": str(e)})

    return Handler


def make_server(app: ReviewApp, port: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))


def serve(deck_path, tally_out, port: int = 0) -> int:
    app = ReviewApp(deck_path, tally_out)
    srv = make_server(app, port)
    print(f"gdt review: {len(app.rows)} rows. Open this in your browser:\n\n"
          f"  http://127.0.0.1:{srv.server_address[1]}/?t={app.token}\n\n"
          f"Answers save as you go. Ctrl+C stops the server.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
