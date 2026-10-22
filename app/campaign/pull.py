"""Pack a campaign's review records for the operator (campaign design §4).

    docker compose exec sindri python -m app.campaign.pull pilot-1 /export/pilot-1.tar

The tar holds, per drawing, the review dir (header, proposal, events,
sealed/) under <campaign>/<doc_hash>/, plus <campaign>/index.json with the
index facts grading needs: group, state, `final_revision` and `sealed_seq`
from the index, and `sealed_revision`, the count of sealed/r*.json on disk.
**Grading trusts `final_revision`** -- it is what the reviewer's Approve
committed; the two are equal except in the crash window between a seal's
file write and its index update, which `sealed_revision` exists to expose.

It is CLIENT DATA under CLAUDE.md §1: it goes into the protected root, and an
agent only ever sees what `runner score --reviewer-grade` (plan C2)
summarises from it. Safe to run mid-campaign: unapproved drawings are simply
unsealed, and a drawing without a complete review record (still queued, or
damaged) is skipped and counted, never fatal. The archive is written as
<out>.partial and renamed into place only once complete."""
import argparse
import io
import json
import os
import sys
import tarfile
from pathlib import Path

from app.batch import db
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.review.store import ReviewStore


def _excluded(path: Path) -> bool:
    # The writer token is a live credential for the session (latest tab
    # wins), not part of the record; *.tmp are the store's atomic-write
    # leftovers, half-written by definition.
    return path.name == "writer" or path.name.endswith(".tmp")


def _sealed_revisions(review_dir: Path) -> int:
    """COUNTED, as ReviewStore._revisions does, never parsed: a corrupt
    rN.json is still a revision the reviewer sealed, and must not cost the
    pull."""
    sealed = review_dir / "sealed"
    return len(list(sealed.glob("r*.json"))) if sealed.is_dir() else 0


def _members(review_dir: Path) -> list:
    """Every file of the record, READ before anything goes into the tar: a
    file that vanishes or cannot be read then skips the drawing, instead of
    leaving a tar header whose data never arrives (a corrupt archive)."""
    out = []
    for p in sorted(review_dir.rglob("*")):
        # The store never writes one; following it could pack a file from
        # outside the record. Treated as damage: the drawing is skipped.
        if p.is_symlink():
            raise OSError("symlink in a review record")
        if p.is_dir():
            continue
        if _excluded(p):
            continue
        out.append((p.relative_to(review_dir).as_posix(), p.read_bytes()))
    return out


def _add(tar, name: str, blob: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(blob)
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(blob))


def pull_campaign(campaign: str, repo: Repo, store: ReviewStore, out_path,
                  skipped: list = None) -> int:
    """Pack and return the number of drawings packed. Drawings that could not
    be packed are appended to `skipped` as (doc id prefix, reason TYPE) --
    never a message, which could carry a path or client text (CLAUDE.md §1)."""
    skipped = [] if skipped is None else skipped
    out_path = Path(out_path)
    partial = out_path.with_name(out_path.name + ".partial")
    docs = [d for d in repo.list_documents() if d["campaign"] == campaign]
    index, used = [], set()
    ok = False
    try:
        with tarfile.open(partial, "w") as tar:
            for d in docs:
                review_dir = store.root / d["id"]
                # The store writes proposal.json LAST: without it the record
                # is a create that never finished (or a drawing still queued).
                if not (review_dir / "proposal.json").is_file():
                    skipped.append((d["id"][:8], "NoReviewRecord"))
                    continue
                try:
                    header = json.loads((review_dir / "header.json").read_text(encoding="utf-8"))
                    facts = header.get("campaign") or {}
                    if not isinstance(facts, dict):
                        raise TypeError("campaign facts are not an object")
                    sealed_revision = _sealed_revisions(review_dir)
                    members = _members(review_dir)
                except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
                    skipped.append((d["id"][:8], type(e).__name__))
                    continue
                h = facts.get("doc_hash") or d["id"][:8]
                # Two drawings can share a doc hash (a re-selection, a rebuilt
                # bundle with changed bytes); one archive dir for both would
                # overwrite one review with the other inside the tar.
                name = h if h not in used else f"{h}-{d['id'][:8]}"
                used.add(name)
                for rel, blob in members:
                    _add(tar, f"{campaign}/{name}/{rel}", blob)
                index.append({"doc_hash": h, "dir": name, "group": facts.get("group"),
                              "state": d["state"], "final_revision": d["final_revision"],
                              "sealed_seq": d["sealed_seq"],
                              "sealed_revision": sealed_revision or None})
            _add(tar, f"{campaign}/index.json",
                 json.dumps({"campaign": campaign, "documents": index}, indent=1).encode())
        os.replace(partial, out_path)
        ok = True
    finally:
        if not ok:
            partial.unlink(missing_ok=True)
    return len(index)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("campaign")
    ap.add_argument("out")
    args = ap.parse_args(argv)
    data = DataDir(os.environ.get("SINDRI_DATA_DIR") or "/data")
    store = ReviewStore(Path(os.environ.get("SINDRI_REVIEW_DIR") or data.root / "reviews"))
    conn = db.connect(data.db_path)
    skipped = []
    try:
        n = pull_campaign(args.campaign, Repo(conn), store, args.out, skipped=skipped)
    finally:
        conn.close()
    for prefix, err in skipped:
        print(f"WARNING: skipped {prefix}: no complete review record ({err})", file=sys.stderr)
    print(f"packed {n} drawings of {args.campaign}, skipped {len(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
