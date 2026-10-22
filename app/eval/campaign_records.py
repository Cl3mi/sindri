"""Read a pulled campaign (app/campaign/pull.py) straight from its tar: no
extraction, so no second copy of client data lands anywhere.

Only `final_revision` decides which sealed revision is graded (pull.py's
docstring: the DB's final_revision is what the reviewer approved). events.jsonl
is read for review TIME only, and a torn last line -- a pull taken mid-append --
is dropped, not fatal.

One damaged drawing must not cost the whole campaign: a missing or unparsable
header/proposal/sealed file becomes `DrawingRecord.error`, a TYPE word such as
"header_missing" -- never exception text, which can quote client values
(CLAUDE.md §1) -- so the grader can report that drawing ungradable and go on."""
import json
import tarfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class DrawingRecord:
    dir: str
    index: dict
    header: dict
    proposal_rows: List[dict]
    sealed: Optional[dict]
    events: List[dict] = field(default_factory=list)
    error: Optional[str] = None     # first damage found; None = record is whole
    # index final_revision != sealed_revision (both set): pull.py's documented
    # crash window between a seal's file write and its index update. Grading
    # trusts final_revision; this lets the report say the pull saw the window.
    revision_mismatch: bool = False


@dataclass
class CampaignRecords:
    campaign: str
    # Keyed by the index's doc_hash, or by `dir` when it is null (pull.py packs
    # a hashless drawing under nohash-<id8>) or already taken (a collision is
    # packed under <hash>-<id8>): two drawings must never share one key.
    drawings: Dict[str, DrawingRecord]
    skipped: List[dict]


def _events(raw: bytes) -> List[dict]:
    """Decoded PER LINE: a pull taken mid-append can cut a multi-byte character
    in the last line, and decoding the whole file would fail the campaign over
    a tail that is meant to be harmless. A line that will not decode, parse or
    is not an object ends the list -- nothing after it can be trusted."""
    out = []
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            e = json.loads(line.decode("utf-8"))
        except ValueError:      # includes UnicodeDecodeError
            break
        if not isinstance(e, dict):
            break
        out.append(e)
    return out


def read_records(tar_path) -> CampaignRecords:
    with tarfile.open(tar_path, "r") as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}

        def raw(name):
            m = members.get(name)
            return tar.extractfile(m).read() if m else None

        def load(name, what, want=dict):
            """(value, None) or (None, error word)."""
            data = raw(name)
            if data is None:
                return None, f"{what}_missing"
            try:
                val = json.loads(data.decode("utf-8"))
                if not isinstance(val, want):
                    raise TypeError
                return val, None
            except (ValueError, TypeError):     # ValueError covers decode errors
                return None, f"{what}_unreadable"

        index_name = next((n for n in members if n.endswith("/index.json")
                           and n.count("/") == 1), None)
        if index_name is None:
            raise ValueError("not a campaign pull: no index.json")
        root = index_name.split("/")[0]
        # One wrap for every way an index can be odd, with no exception text:
        # the index carries doc hashes and groups, and a message could quote them.
        try:
            index = json.loads(raw(index_name).decode("utf-8"))
            docs = index["documents"]
            skipped = index.get("skipped", [])
            if not (isinstance(docs, list) and isinstance(skipped, list)
                    and all(isinstance(d, dict) and isinstance(d.get("dir"), str)
                            for d in docs)):
                raise TypeError
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError("not a campaign pull: unreadable index.json") from None
        drawings = {}
        for d in docs:
            base = f"{root}/{d['dir']}"
            errors = []
            header, e = load(f"{base}/header.json", "header")
            errors.append(e)
            proposal, e = load(f"{base}/proposal.json", "proposal")
            errors.append(e)
            rows = proposal.get("rows") if proposal else None
            if proposal is not None and not isinstance(rows, list):
                errors.append("proposal_unreadable")
            rev = d.get("final_revision")
            sealed = None
            if rev:
                sealed, e = load(f"{base}/sealed/r{rev}.json", "sealed")
                errors.append(e)
            key = d.get("doc_hash") or d["dir"]
            if key in drawings:
                key = d["dir"]
            sr = d.get("sealed_revision")
            drawings[key] = DrawingRecord(
                dir=d["dir"], index=d, header=header or {},
                proposal_rows=rows if isinstance(rows, list) else [],
                sealed=sealed,
                events=_events(raw(f"{base}/events.jsonl") or b""),
                error=next((x for x in errors if x), None),
                revision_mismatch=bool(rev and sr and rev != sr))
        return CampaignRecords(campaign=index.get("campaign", root),
                               drawings=drawings, skipped=skipped)
