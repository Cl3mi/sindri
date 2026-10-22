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


@dataclass
class CampaignRecords:
    campaign: str
    # Keyed by the index's doc_hash, or by `dir` when it is null (pull.py packs
    # a hashless drawing under nohash-<id8>) or already taken (a collision is
    # packed under <hash>-<id8>): two drawings must never share one key.
    drawings: Dict[str, DrawingRecord]
    skipped: List[dict]


def _events(text: str) -> List[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            break       # a torn tail; nothing after it can be trusted either
    return out


def read_records(tar_path) -> CampaignRecords:
    with tarfile.open(tar_path, "r") as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}

        def text(name):
            m = members.get(name)
            return tar.extractfile(m).read().decode("utf-8") if m else None

        def load(name, what, errors, want=dict):
            """(value, None) or (None, error word); the first error is kept."""
            raw = text(name)
            if raw is None:
                return None, f"{what}_missing"
            try:
                val = json.loads(raw)
                if not isinstance(val, want):
                    raise TypeError
                return val, None
            except (ValueError, TypeError, UnicodeDecodeError):
                return None, f"{what}_unreadable"

        index_name = next((n for n in members if n.endswith("/index.json")
                           and n.count("/") == 1), None)
        if index_name is None:
            raise ValueError("not a campaign pull: no index.json")
        root = index_name.split("/")[0]
        index = json.loads(text(index_name))
        drawings = {}
        for d in index.get("documents", []):
            base = f"{root}/{d['dir']}"
            errors = []
            header, e = load(f"{base}/header.json", "header", errors)
            errors.append(e)
            proposal, e = load(f"{base}/proposal.json", "proposal", errors)
            errors.append(e)
            rows = proposal.get("rows") if proposal else None
            if proposal is not None and not isinstance(rows, list):
                errors.append("proposal_unreadable")
            rev = d.get("final_revision")
            sealed = None
            if rev:
                sealed, e = load(f"{base}/sealed/r{rev}.json", "sealed", errors)
                errors.append(e)
            key = d.get("doc_hash") or d["dir"]
            if key in drawings:
                key = d["dir"]
            drawings[key] = DrawingRecord(
                dir=d["dir"], index=d, header=header or {},
                proposal_rows=rows if isinstance(rows, list) else [],
                sealed=sealed,
                events=_events(text(f"{base}/events.jsonl") or ""),
                error=next((x for x in errors if x), None))
        return CampaignRecords(campaign=index.get("campaign", root),
                               drawings=drawings, skipped=index.get("skipped", []))
