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


def _fsync_dir(path: Path) -> None:
    # A rename is atomic but not DURABLE until the directory entry for it is
    # fsynced too -- on a crash, the file's own data can be safely on disk
    # while the rename that made it visible under its final name is still
    # only in the directory's write-back cache and can be lost.
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: str) -> None:
    # Write-then-rename, so a crash never leaves a half-written record that a
    # later grade would read as complete. fsync the tmp file's data before
    # the rename (the client discards an event once it is acknowledged, so
    # an ack must survive power loss, not just a process crash), and fsync
    # the directory after the rename, for the reason _fsync_dir explains.
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _write_json(path: Path, obj) -> None:
    _atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=1))


class ReviewStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # FastAPI runs sync endpoints on a thread pool, and the UI's pagehide
        # beacon can race a normal flush for the same session; without this,
        # two threads can both read the same "have" set and both write the
        # same seq (a duplicated retract then makes net_events raise at seal
        # and the reviewer can never Finish). Assumes a single worker process
        # -- it protects threads sharing this instance, not separate processes
        # (cross-process locking is deferred; Phase 2).
        self._lock = threading.Lock()

    # ----- layout ------------------------------------------------------
    def _dir(self, sid: str) -> Path:
        d = self.root / sid
        if not (d / "proposal.json").is_file():
            raise UnknownSession(f"no review record for session {sid[:8]}")
        return d

    def _check_writer(self, d: Path, writer) -> None:
        try:
            stored = (d / "writer").read_text(encoding="utf-8")
        except OSError:
            # A missing or unreadable writer file is not this server's crash
            # to expose as a 500 -- to the caller it is indistinguishable
            # from "someone else's token", so refuse the same way.
            raise StaleWriter("this drawing was opened elsewhere; reload to continue") from None
        if not isinstance(writer, str) or not secrets.compare_digest(
                stored.encode("utf-8"), writer.encode("utf-8")):
            raise StaleWriter("this drawing was opened elsewhere; reload to continue")

    def _events(self, d: Path) -> List[dict]:
        f = d / "events.jsonl"
        if not f.is_file():
            return []
        text = f.read_text(encoding="utf-8")
        # Split only on "\n". str.splitlines() also breaks on U+2028,
        # U+2029, U+0085 and others, which an edit_cell's free-text value can
        # legitimately contain; json.dumps writes those raw unless told
        # otherwise, so a splitlines()-based read would slice one valid JSON
        # line into fragments that fail to parse as "corrupt" lines.
        lines = [l for l in text.split("\n") if l.strip()]
        events = []
        for i, l in enumerate(lines):
            try:
                e = json.loads(l)
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
            try:
                validate_event(e)
            except (JournalError, TypeError, KeyError) as exc:
                # Unlike a JSON syntax error, this line parsed cleanly -- a
                # crash cannot tear a write into a complete-but-wrong-shape
                # object, so this is external corruption regardless of
                # position, including on the last line.
                raise JournalInvalid(
                    f"corrupt journal: line {i + 1} is not a valid event ({exc})"
                ) from exc
            events.append(e)
        return events

    def _repair_torn_tail(self, d: Path) -> None:
        # Operate on the RAW text, not on split lines: a crash can leave the
        # file not ending in "\n" in two different shapes that only the raw
        # bytes distinguish --
        #   (a) a COMPLETE line simply missing its trailing newline (the
        #       write returned before the separator landed), or
        #   (b) a genuinely torn partial line.
        # Checking only "does the last line parse" (as an earlier version of
        # this method did) cannot tell them apart once further lines are
        # appended: a later write lands directly onto (a) with no separator,
        # merging two JSON objects into one unparsable line, which then
        # looks torn in turn and gets erased -- losing BOTH events.
        f = d / "events.jsonl"
        if not f.is_file():
            return
        text = f.read_text(encoding="utf-8")
        if not text or text.endswith("\n"):
            return
        head, sep, tail = text.rpartition("\n")
        try:
            json.loads(tail)
        except json.JSONDecodeError:
            # Genuinely torn: drop the unterminated fragment, keep the rest.
            new_text = head + sep
        else:
            # A complete event that just never got its newline.
            new_text = text + "\n"
        _atomic_write(f, new_text)

    def _sealed_through(self, d: Path) -> int:
        seals = sorted((d / "sealed").glob("r*.json")) if (d / "sealed").is_dir() else []
        return max(
            (json.loads(p.read_text(encoding="utf-8"))["final_seq"] for p in seals),
            default=0)

    def _revisions(self, d: Path) -> int:
        return len(list((d / "sealed").glob("r*.json"))) if (d / "sealed").is_dir() else 0

    # ----- operations --------------------------------------------------
    def create(self, sid: str, header: dict, proposal: dict) -> str:
        with self._lock:
            d = self.root / sid
            # _revisions (files on disk), not "sealed dir exists": a crash
            # right after mkdir(sealed) but before any revision is written
            # would otherwise lock the session as "sealed" forever.
            if self._revisions(d) > 0:
                raise JournalInvalid("session already sealed; a re-extraction needs a new session")
            if d.exists():
                events_path = d / "events.jsonl"
                if events_path.is_file() and events_path.read_text(encoding="utf-8").strip():
                    # The reviewer has already acted on this session; a
                    # re-extraction must not silently erase that work.
                    raise JournalInvalid(
                        "session already has review events; a re-extraction needs a new session")
                # Untouched, unsealed session (e.g. a retried extraction
                # before any review happened): nothing has reviewed it yet,
                # so it is safe to replace outright.
                shutil.rmtree(d)
            d.mkdir(parents=True)
            _write_json(d / "header.json", header)
            writer = secrets.token_hex(16)
            _atomic_write(d / "writer", writer)
            # proposal.json LAST: _dir() treats its existence as "this
            # session exists", so a crash earlier in create() must not look
            # like a finished one.
            _write_json(d / "proposal.json", proposal)
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
            # A resend of a seq already on disk is ignored -- first write
            # wins, which is sound because a session has a single writer.
            new = [e for e in events if e["seq"] > through and e["seq"] not in have]
            if new:
                events_path = d / "events.jsonl"
                # Only a brand-new events.jsonl needs its directory entry
                # fsynced -- every append after the first is appending to an
                # already-durable name, not creating one.
                creating = not events_path.is_file()
                with events_path.open("a", encoding="utf-8") as f:
                    for e in new:
                        # ensure_ascii=True: pure-ASCII lines cost nothing and
                        # guarantee a value's raw bytes never contain a
                        # character str.split("\n") would not also split on.
                        f.write(json.dumps(e) + "\n")
                        have.add(e["seq"])
                    f.flush()
                    os.fsync(f.fileno())
                if creating:
                    _fsync_dir(d)
            return contiguous_seq(have, start=through)

    def seal(self, sid: str, writer: str, final_seq: int,
             rows: List[dict], reviewed_ids: List[str]) -> dict:
        with self._lock:
            d = self._dir(sid)
            self._check_writer(d, writer)
            through = self._sealed_through(d)
            if (not isinstance(final_seq, int) or isinstance(final_seq, bool)
                    or final_seq < through):
                raise JournalInvalid(f"bad final_seq {final_seq!r}")
            self._repair_torn_tail(d)
            events = self._events(d)
            seqs = {e["seq"] for e in events}
            if contiguous_seq(seqs, start=through) < final_seq:
                raise JournalGap("the review log is missing events; Finish again once it has caught up")
            if any(s > final_seq for s in seqs):
                raise JournalInvalid("the review log holds events after the final one")
            try:
                net = net_events(events)
                proposal = json.loads(
                    (d / "proposal.json").read_text(encoding="utf-8"))["rows"]
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
            _atomic_write(
                d / "events.jsonl",
                "".join(json.dumps(e) + "\n" for e in net))
            return {"revision": rev, "replay_ok": not bad}
