"""The operator's drill-down into a graded campaign (C2 design, Operator
drill-down): which example rows each correction bucket shows (`examples`,
pure), and the localhost server that shows them with crops of the ORIGINAL
drawing (`CampaignDeckApp`, `serve`).

Everything here holds client values (proposed/final rows, notes), so it is
the OPERATOR's: `runner campaign-deck` is deliberately absent from the agent
guard's allowlist, and an agent never starts it or accepts its link. The
server copies review_server's security exactly -- 127.0.0.1 only, a Host
check against DNS rebinding, a random token on every request, no-store,
no-referrer, a CSP forbidding every external request, no request logging --
and writes no file: the report is read once, crops are rendered in memory."""
import hmac
import json
import math
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from app.eval import anon, review, review_crops
from app.eval.campaign_grade import (REPORT_KIND, _KEY, _phantom_reason,
                                     _same_frame, _scale_ok, px_to_mm, ranked_findings,
                                     row_buckets)
from app.eval.dump import to_points
from app.review.journal import EDIT_REASONS

DEFAULT_PER_BUCKET = 12


def _reason_word(grade, reason) -> str:
    """A closed-vocabulary word, never the record's own string: the sealed
    journal is read raw, so a reason is only shown when it is one of the
    journal's words. Edits and deletes have different vocabularies."""
    if grade in ("edited", "suggestion_edited", "suggestion_confirmed"):
        if reason is None:
            return "none"
        return reason if isinstance(reason, str) and reason in EDIT_REASONS else "other"
    return _phantom_reason(reason)


def _by_id(rows) -> Dict[object, dict]:
    return {r.get("id"): r for r in rows or [] if isinstance(r, dict)}


def examples(report: dict, per_bucket: int = DEFAULT_PER_BUCKET) -> dict:
    """-> {"buckets": {bucket: {"total": n, "examples": [...]}}, "order": [...]}.

    Buckets come from `row_buckets`, the same definition `ranked_findings`
    counts with, so each `total` equals the digest's count for that bucket
    and `order` is the digest's ranked order (value table, then placement).
    Examples are capped at `per_bucket`; the total says what the cap hid.

    Pick order is deterministic: placement by displacement (bubble, farthest
    first) or overlap (anchors, least first), value buckets by report order
    then row id. Ungradable and pending drawings carry no rows and are
    skipped."""
    drawings = [d for d in report.get("drawings", []) if d.get("rows") is not None]
    # {bucket: [(sort_key, example)]}
    found: Dict[str, List[tuple]] = {}

    def add(bucket, key, d, row_id, grade, reason, prop, fin, mm, verdict):
        found.setdefault(bucket, []).append((key, {
            "doc_hash": d.get("doc_hash"), "group": d.get("group"), "row_id": row_id,
            "grade": grade, "reason": reason, "proposed": prop, "final": fin,
            "mm": mm, "scale": d.get("scale"), "verdict": verdict,
            "note": d.get("note") or ""}))

    for di, d in enumerate(drawings):
        scale = d["scale"]
        proposal, final = _by_id(d.get("proposal")), _by_id(d.get("final"))
        cross = d.get("cross")
        verdicts = (cross.get("verdicts") if isinstance(cross, dict)
                    and cross.get("computed") is True else None) or {}

        def verdict(rid) -> Optional[str]:
            v = verdicts.get(rid)
            return v if isinstance(v, str) else None

        for r in d["rows"]:
            rid = r["id"]
            mm = (px_to_mm(r["bubble_px"], scale) if r.get("bubble_px") is not None
                  else None)
            for kind, bucket in row_buckets(r, scale):
                if bucket == "bubble_moved":
                    key = (-mm, di, str(rid))
                elif kind == "placement":
                    key = (r["anchor_iou"], di, str(rid))
                else:
                    key = (di, str(rid))
                add(bucket, key, d, rid, r["grade"], _reason_word(r["grade"], r.get("reason")),
                    proposal.get(rid), final.get(rid), mm, verdict(rid))
        for m in d["missed"]:
            rid = m.get("id")
            # Added by the client, so there is no proposal and no reason.
            add("missed", (di, str(rid)), d, rid, "missed", "none", None, m, None,
                verdict(rid))

    buckets = {}
    for bucket, items in found.items():
        items.sort(key=lambda kv: kv[0])
        buckets[bucket] = {"total": len(items),
                           "examples": [e for _, e in items[:per_bucket]]}
    ranked = ranked_findings(drawings)
    order = [r["bucket"] for kind in ("value", "placement") for r in ranked[kind]]
    return {"buckets": {b: buckets[b] for b in order}, "order": order}


# --- the server ---------------------------------------------------------------

_PAGE = Path(__file__).with_name("campaign_deck_page.html")
_CSP = ("default-src 'none'; script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; img-src 'self'; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
# Fixed sentences only, here and in every refusal: whatever this prints may be
# read by someone other than the operator, and a path, a stem or exception
# text can name a client part.
MSG_NO_DRAWING = "drawing not available"
MSG_FRAME = "page does not match the campaign"
MSG_NO_POSITION = "no position recorded"
# A pull dir is `<hash>`, `<hash>-<id8>` on a collision, or `nohash-<id8>`
# (campaign_grade._KEY): only the first two name a drawing to look up.
_HASH_OF_KEY = re.compile(r"\A([0-9a-f]{8})(?:-[0-9a-f]{8})?\Z")


def _refuse(msg: str):
    raise review.ReviewRefused(msg)


def _load_report(path) -> dict:
    p = Path(path)
    # Before resolving, or a link is followed to whatever it names.
    if p.is_symlink():
        _refuse("the grading report path is a symbolic link")
    if not review.inside_protected(p):
        _refuse("the grading report must be inside a protected root")
    # The marker is the report's FIRST key, read from the opening bytes only,
    # so a file that is not ours is refused without being parsed.
    if not review._carries_marker(p.resolve(), REPORT_KIND):
        _refuse("that file is not a campaign grading report")
    try:
        report = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = None
    if not isinstance(report, dict) or not isinstance(report.get("drawings"), list):
        _refuse("the grading report could not be read")
    return report


def _nums(v, n) -> Optional[tuple]:
    if (not isinstance(v, (list, tuple)) or len(v) != n
            or any(isinstance(x, bool) or not isinstance(x, (int, float))
                   or not math.isfinite(x) for x in v)):
        return None
    return tuple(float(x) for x in v)


def _levers(report) -> Dict[str, dict]:
    """bucket -> lever text from the report's own digest findings (fixed
    words we wrote), so the page names the lever the digest ranked."""
    findings = (report.get("digest") or {}).get("findings") if isinstance(
        report.get("digest"), dict) else None
    out = {}
    for kind in ("value", "placement"):
        rows = findings.get(kind) if isinstance(findings, dict) else None
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict) and isinstance(r.get("bucket"), str):
                out[r["bucket"]] = {"kind": kind, "count": r.get("count"),
                                    "lever": r.get("lever"),
                                    "lever_status": r.get("lever_status")}
    return out


class CampaignDeckApp:
    def __init__(self, report_path, pdfs_dir, salt: Optional[str] = None):
        self.report = _load_report(report_path)
        pdfs = Path(pdfs_dir)
        if not pdfs.is_dir():
            _refuse("the --pdfs argument must be a directory")
        # Without the operator's own salt no hash joins to a drawing, and
        # Anonymizer() would MINT a throwaway salt file: check, never create.
        if salt is None and not anon.salt_is_persistent():
            _refuse("no persistent doc-id salt on this machine; copy the operator's salt first")
        try:
            self.examples = examples(self.report)
        except Exception:  # noqa: BLE001 -- a damaged row would quote itself
            _refuse("the grading report could not be read")
        self.levers = _levers(self.report)
        self.token = secrets.token_urlsafe(18)
        self.drawings: Dict[str, dict] = {}
        for d in self.report["drawings"]:
            if isinstance(d, dict) and isinstance(d.get("doc_hash"), str):
                self.drawings.setdefault(d["doc_hash"], d)
        # Only (doc, row) pairs the page shows are croppable: never a free id.
        self.shown: Dict[tuple, dict] = {}
        for b in self.examples["buckets"].values():
            for e in b["examples"]:
                if isinstance(e["doc_hash"], str) and _KEY.match(e["doc_hash"]):
                    self.shown.setdefault((e["doc_hash"], str(e["row_id"])), e)
        # Stems are hashed, never kept by name in anything a response carries.
        a = anon.Anonymizer(salt=salt)
        self.pdfs: Dict[str, Path] = {}
        for p in sorted(pdfs.iterdir()):
            if p.suffix.lower() == ".pdf" and p.is_file():
                self.pdfs.setdefault(a(p.stem), p)

    def payload(self) -> dict:
        return {**self.examples, "levers": self.levers}

    def _pdf_for(self, key: str) -> Optional[Path]:
        m = _HASH_OF_KEY.match(key)
        return self.pdfs.get(m.group(1)) if m else None

    def crop(self, key: str, row_id: str) -> bytes:
        ex, d = self.shown[(key, row_id)], self.drawings.get(key) or {}
        pdf = self._pdf_for(key)
        page = review_crops.page_rect(pdf) if pdf is not None else None
        if page is None:
            return review_crops.placeholder_png(MSG_NO_DRAWING)
        # The report's px live in the page the CAMPAIGN rendered; on any other
        # sheet every box lands somewhere unrelated, so show no crop at all
        # (_same_frame is the cross-check's rule, 1 pt of rounding).
        frame = d.get("page_rect")
        if not _same_frame(frame, page):
            return review_crops.placeholder_png(MSG_FRAME)
        scale = d.get("scale")
        if not _scale_ok(scale):
            return review_crops.placeholder_png(MSG_NO_POSITION)

        def box(row):
            b = _nums(row.get("target_region"), 4) if isinstance(row, dict) else None
            return to_points(b, scale, frame) if b else None

        def dot(row):
            xy = _nums(row.get("balloon_xy"), 2) if isinstance(row, dict) else None
            return (frame[0] + xy[0] / scale, frame[1] + xy[1] / scale) if xy else None

        prop, fin = ex.get("proposed"), ex.get("final")
        pbox, fbox, pdot, fdot = box(prop), box(fin), dot(prop), dot(fin)
        spans = [b for b in (pbox, fbox) if b] + [(x, y, x, y) for x, y in
                                                  (p for p in (pdot, fdot) if p)]
        if not spans:
            return review_crops.placeholder_png(MSG_NO_POSITION)
        union = (min(s[0] for s in spans), min(s[1] for s in spans),
                 max(s[2] for s in spans), max(s[3] for s in spans))
        # crop_rect pads by PAD_PT (40 pt), grows to a readable minimum and
        # keeps the region on the page.
        rect = review_crops.crop_rect(page, union, None)
        try:
            return review_crops.render_crop(pdf, rect, pred_box_pt=pbox, gold_pt=fdot,
                                            final_box_pt=fbox)
        except review_crops.CropError:
            return review_crops.placeholder_png(MSG_NO_DRAWING)


def make_handler(app: CampaignDeckApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "sindri-campaign-deck"

        def log_message(self, *args):
            pass        # request lines carry the token and doc hashes

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
            return url

        def do_GET(self):
            url = self._gate()
            if url is None:
                return
            if url.path == "/":
                self._send(200, _PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/examples":
                self._json(200, app.payload())
            elif url.path == "/crop":
                q = parse_qs(url.query)
                key = q.get("doc", [""])[0]
                rid = q.get("row", [""])[0]
                # Shape first, then membership: the answer never depends on,
                # or echoes, anything but a pair the report itself shows.
                if _KEY.match(key) and (key, rid) in app.shown:
                    self._send(200, app.crop(key, rid), "image/png")
                else:
                    self._json(404, {"error": "not found"})
            else:
                self._json(404, {"error": "not found"})

    return Handler


def make_server(app: CampaignDeckApp, port: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))


def serve(report_path, pdfs_dir, port: int = 0) -> int:
    # Validation runs in the constructor, before anything binds.
    app = CampaignDeckApp(report_path, pdfs_dir)
    srv = make_server(app, port)
    print(f"campaign deck. Open this in your browser:\n\n"
          f"  http://127.0.0.1:{srv.server_address[1]}/?t={app.token}\n\n"
          f"Ctrl+C stops the server.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
