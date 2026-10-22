"""Pack a campaign's review records for the operator (campaign design §4).

    docker compose exec sindri python -m app.campaign.pull pilot-1 /export/pilot-1.tar

The tar holds, per drawing, the review dir (header, proposal, events,
sealed/) under <campaign>/<doc_hash>/, plus <campaign>/index.json with the
index facts grading needs (group, state, latest sealed revision). It is
CLIENT DATA under CLAUDE.md §1: it goes into the protected root, and an agent
only ever sees what `runner score --reviewer-grade` (plan C2) summarises from
it. Safe to run mid-campaign: unapproved drawings are simply unsealed, and a
drawing with no review record yet is skipped and counted."""
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
from app.review.store import ReviewStore, UnknownSession

# The writer token is a live credential for the session (latest tab wins),
# not part of the record; nothing downstream needs it.
_EXCLUDED = {"writer"}


def _latest_revision(store: ReviewStore, sid: str):
    try:
        return store.revision(sid)["revision"]
    except UnknownSession:
        return None


def pull_campaign(campaign: str, repo: Repo, store: ReviewStore, out_path,
                  skipped: list = None) -> int:
    """Pack and return the number of drawings packed. Drawings that could not
    be packed are appended to `skipped` as (doc id prefix, error type)."""
    skipped = [] if skipped is None else skipped
    docs = [d for d in repo.list_documents() if d["campaign"] == campaign]
    index, used = [], set()

    def keep(info):
        return None if Path(info.name).name in _EXCLUDED else info

    with tarfile.open(out_path, "w") as tar:
        for d in docs:
            review_dir = store.root / d["id"]
            try:
                header = json.loads((review_dir / "header.json").read_text(encoding="utf-8"))
                facts = header.get("campaign") or {}
                if not isinstance(facts, dict):
                    raise TypeError("campaign facts are not an object")
            except (OSError, ValueError, TypeError, AttributeError) as e:
                # TYPE only (CLAUDE.md §1). Still queued, or a damaged record.
                skipped.append((d["id"][:8], type(e).__name__))
                continue
            h = facts.get("doc_hash") or d["id"][:8]
            # Two drawings can share a doc hash (a re-selection, a rebuilt
            # bundle with changed bytes); one archive dir for both would
            # overwrite one review with the other inside the tar.
            name = h if h not in used else f"{h}-{d['id'][:8]}"
            used.add(name)
            tar.add(review_dir, arcname=f"{campaign}/{name}", filter=keep)
            index.append({"doc_hash": h, "dir": name, "group": facts.get("group"),
                          "state": d["state"], "final_revision": d["final_revision"],
                          "sealed_seq": d["sealed_seq"],
                          "sealed_revision": _latest_revision(store, d["id"])})
        blob = json.dumps({"campaign": campaign, "documents": index}, indent=1).encode()
        info = tarfile.TarInfo(f"{campaign}/index.json")
        info.size = len(blob)
        tar.addfile(info, io.BytesIO(blob))
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
        print(f"WARNING: skipped {prefix}: no readable review header ({err})", file=sys.stderr)
    print(f"packed {n} drawings of {args.campaign}, skipped {len(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
