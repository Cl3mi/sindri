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
import threading
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
        # FastAPI runs sync endpoints on a thread pool, and the UI's pagehide
        # beacon can race a normal flush for the same session; without this,
        # two threads can both read the same "have" set and both write the
        # same seq (a duplicated retract then makes net_events raise at seal
        # and the reviewer can never Finish). Assumes a single worker process
        # -- it protects threads sharing this instance, not separate processes.
        self._lock = threading.Lock()

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
        lines = [l for l in f.read_text().splitlines() if l.strip()]
        events = []
        for i, l in enumerate(lines):
            try:
                events.append(json.loads(l))
            except json.JSONDecodeError:
                # A crash mid-append can only tear the LAST line -- that
                # batch was never acknowledged, so the client resends it, and
                # refusing the whole session over it would lose a drawing's
                # review for nothing. A broken line anywhere else was never
                # an in-flight write; it is corruption and must raise.
                if i == len(lines) - 1:
                    break
                raise JournalInvalid(
                    f"corrupt journal: line {i + 1} is not valid JSON") from None
        return events

    def _repair_torn_tail(self, d: Path) -> None:
        # Drop a torn trailing line from disk once it is seen, so a
        # subsequent append does not write fresh lines after it -- which
        # would turn forgivable tail garbage into an unforgivable MIDDLE
        # line the next time this file is read.
        f = d / "events.jsonl"
        if not f.is_file():
            return
        lines = [l for l in f.read_text().splitlines() if l.strip()]
        if not lines:
            return
        try:
            json.loads(lines[-1])
        except json.JSONDecodeError:
            tmp = d / "events.jsonl.tmp"
            tmp.write_text("".join(l + "\n" for l in lines[:-1]))
            os.replace(tmp, f)

    def _sealed_through(self, d: Path) -> int:
        seals = sorted((d / "sealed").glob("r*.json")) if (d / "sealed").is_dir() else []
        return max((json.loads(p.read_text())["final_seq"] for p in seals), default=0)

    def _revisions(self, d: Path) -> int:
        return len(list((d / "sealed").glob("r*.json"))) if (d / "sealed").is_dir() else 0

    # ----- operations --------------------------------------------------
    def create(self, sid: str, header: dict, proposal: dict) -> str:
        with self._lock:
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
        with self._lock:
            d = self._dir(sid)
            self._check_writer(d, writer)
            events = list(events)
            try:
                for e in events:
                    validate_event(e)
            except JournalError as exc:
                raise JournalInvalid(str(exc)) from exc
            self._repair_torn_tail(d)
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
        with self._lock:
            d = self._dir(sid)
            self._check_writer(d, writer)
            self._repair_torn_tail(d)
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
