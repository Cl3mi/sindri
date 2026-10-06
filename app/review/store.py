"""Durable, server-held review records (design §2).

    <root>/<session_id>/header.json      server, at extract
                        proposal.json    server, at extract -- never client-supplied
                        writer           the session's single-writer token
                        events.jsonl     appended from the UI; net form after a seal
                        sealed/rN.json   one per Finish

Everything here is CLIENT DATA under the corpus rules (CLAUDE.md §1)."""
import json
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

from app.review.journal import (
    JournalError, contiguous_seq, net_events, validate_event)
from app.review.replay import mismatches, replay


class ReviewError(Exception):
    status = 400


class UnknownSession(ReviewError):
    status = 404


class StaleWriter(ReviewError):
    status = 409


class JournalGap(ReviewError):
    status = 409


class JournalInvalid(ReviewError):
    status = 422


def _write_json(path: Path, obj) -> None:
    # Write-then-rename, so a crash never leaves a half-written record that a
    # later grade would read as complete.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


class ReviewStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ----- layout ------------------------------------------------------
    def _dir(self, sid: str) -> Path:
        d = self.root / sid
        if not (d / "proposal.json").is_file():
            raise UnknownSession(f"no review record for session {sid[:8]}")
        return d

    def _check_writer(self, d: Path, writer: str) -> None:
        if not secrets.compare_digest((d / "writer").read_text(), writer or ""):
            raise StaleWriter("this drawing was opened elsewhere; reload to continue")

    def _events(self, d: Path) -> List[dict]:
        f = d / "events.jsonl"
        if not f.is_file():
            return []
        return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]

    def _sealed_through(self, d: Path) -> int:
        seals = sorted((d / "sealed").glob("r*.json")) if (d / "sealed").is_dir() else []
        return max((json.loads(p.read_text())["final_seq"] for p in seals), default=0)

    def _revisions(self, d: Path) -> int:
        return len(list((d / "sealed").glob("r*.json"))) if (d / "sealed").is_dir() else 0

    # ----- operations --------------------------------------------------
    def create(self, sid: str, header: dict, proposal: dict) -> str:
        d = self.root / sid
        if (d / "sealed").is_dir():
            raise JournalInvalid("session already sealed; a re-extraction needs a new session")
        if d.exists():
            # Re-extraction of an unsealed session: the old proposal no longer
            # describes what the reviewer sees, so its journal must go with it.
            shutil.rmtree(d)
        d.mkdir(parents=True)
        _write_json(d / "header.json", header)
        _write_json(d / "proposal.json", proposal)
        writer = secrets.token_hex(16)
        (d / "writer").write_text(writer)
        return writer

    def append(self, sid: str, writer: str, events: Iterable[dict]) -> int:
        d = self._dir(sid)
        self._check_writer(d, writer)
        events = list(events)
        try:
            for e in events:
                validate_event(e)
        except JournalError as exc:
            raise JournalInvalid(str(exc)) from exc
        through = self._sealed_through(d)
        have = {e["seq"] for e in self._events(d)}
        new = [e for e in events if e["seq"] > through and e["seq"] not in have]
        if new:
            with (d / "events.jsonl").open("a") as f:
                for e in new:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
                    have.add(e["seq"])
        return contiguous_seq(have, start=through)

    def seal(self, sid: str, writer: str, final_seq: int,
             rows: List[dict], reviewed_ids: List[str]) -> dict:
        d = self._dir(sid)
        self._check_writer(d, writer)
        events = self._events(d)
        seqs = {e["seq"] for e in events}
        through = self._sealed_through(d)
        if contiguous_seq(seqs, start=through) < final_seq:
            raise JournalGap("the review log is missing events; Finish again once it has caught up")
        if any(s > final_seq for s in seqs):
            raise JournalInvalid("the review log holds events after the final one")
        try:
            net = net_events(events)
            proposal = json.loads((d / "proposal.json").read_text())["rows"]
            # Replay from the PROPOSAL through every surviving event, including
            # those kept from earlier seals, so revision N describes the whole
            # session, not only what changed since N-1.
            bad = mismatches(replay(proposal, net), rows, reviewed_ids)
        except JournalError as exc:
            raise JournalInvalid(str(exc)) from exc
        rev = self._revisions(d) + 1
        (d / "sealed").mkdir(exist_ok=True)
        _write_json(d / "sealed" / f"r{rev}.json", {
            "revision": rev,
            "sealed_at": datetime.now(timezone.utc).isoformat(),
            "final_seq": final_seq,
            "rows": rows,
            "reviewed_ids": sorted(reviewed_ids),
            "net_events": net,
            "replay_mismatch_ids": bad,
        })
        tmp = d / "events.jsonl.tmp"
        tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in net))
        os.replace(tmp, d / "events.jsonl")
        return {"revision": rev, "replay_ok": not bad}
