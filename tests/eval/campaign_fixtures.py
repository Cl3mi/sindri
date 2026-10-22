"""Synthetic pull tars for C2 tests -- the layout app/campaign/pull.py writes
(`<campaign>/<dir>/{header,proposal}.json`, `events.jsonl`, `sealed/rN.json`,
plus `<campaign>/index.json`), built from dicts so no client data is needed.

Two details copy the real pull rather than the obvious guess: `sealed_revision`
is the COUNT of sealed files or None (never 0), and a drawing may be packed
under `nohash-<id8>` with `doc_hash: null`, or `<hash>-<id8>` on a collision,
so `dir` is its own field and never assumed equal to the hash."""
import io
import json
import tarfile


def proposal_row(id, **kw):
    base = {"id": id, "pos": 1, "char_type": "Distance", "nominal": "10",
            "upper_tol": "+0.1", "lower_tol": "-0.1", "needs_review": False,
            "suggested": False, "balloon_xy": [100.0, 100.0],
            "target_region": [10.0, 10.0, 50.0, 30.0], "source": "auto",
            "kind": "dimension", "confidence": 0.99}
    return {**base, **kw}


def event(seq, op="edit", t_ms=None, **kw):
    """A journal line as app/static/js/journal.js writes it: seq plus the
    wall-clock and monotonic stamps review time is computed from."""
    return {"seq": seq, "t_wall": 1_700_000_000_000 + 1000 * seq,
            "t_ms": 1000 * seq if t_ms is None else t_ms, "op": op, **kw}


def campaign_facts(doc_hash, group="great", render_scale=300 / 72.0, **kw):
    return {"campaign": "pilot-1", "group": group, "source_run": "r5-control",
            "doc_hash": doc_hash, "file_name": f"{doc_hash}.pdf",
            "render_scale": render_scale, "page_rect": [0.0, 0.0, 842.0, 595.0],
            "page_px": [3508, 2479], "run_config": {}, "policy": {}, **kw}


def drawing(doc_hash, proposal, final, net_events, *, group="great",
            events_lines=None, approved=True, mismatch=(), dir=None,
            header=True, **facts):
    """`doc_hash=None` models a drawing whose header names none (packed under
    `nohash-...`); pass `dir` for it or for a collision name."""
    return {"doc_hash": doc_hash, "group": group, "approved": approved,
            "dir": dir or doc_hash,
            "header": ({"review_schema_version": 1,
                        "campaign": campaign_facts(doc_hash, group, **facts)}
                       if header else None),
            "proposal": {"rows": proposal},
            "sealed": {"revision": 1, "rows": final, "net_events": net_events,
                       "reviewed_ids": [], "replay_mismatch_ids": list(mismatch),
                       "final_seq": len(net_events), "sealed_at": "t"},
            "events_lines": events_lines if events_lines is not None else
                            [json.dumps(e) for e in net_events]}


def write_tar(path, drawings, skipped=(), campaign="pilot-1"):
    docs = []
    with tarfile.open(path, "w") as tar:
        def add(name, data: bytes):
            info = tarfile.TarInfo(f"{campaign}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for d in drawings:
            base = d["dir"]
            if d["header"] is not None:
                add(f"{base}/header.json", json.dumps(d["header"]).encode())
            add(f"{base}/proposal.json", json.dumps(d["proposal"]).encode())
            add(f"{base}/events.jsonl", "\n".join(d["events_lines"]).encode())
            if d["approved"]:
                add(f"{base}/sealed/r1.json", json.dumps(d["sealed"]).encode())
            docs.append({"doc_hash": d["doc_hash"], "dir": base, "group": d["group"],
                         "state": "finished" if d["approved"] else "in_review",
                         "final_revision": 1 if d["approved"] else None,
                         "sealed_seq": len(d["sealed"]["net_events"]) if d["approved"] else 0,
                         "sealed_revision": 1 if d["approved"] else None})
        add("index.json", json.dumps({"campaign": campaign, "documents": docs,
                                      "skipped": list(skipped)}).encode())
    return path
