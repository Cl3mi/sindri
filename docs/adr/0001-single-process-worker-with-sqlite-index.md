# 0001. Extraction runs in one background worker thread, one drawing at a time, in a single process, with a SQLite index

Status: Accepted
Date: 2026-10-07

## Context

Until batch B1, a drawing lived and died with one browser tab. `POST /api/extract/{id}`
streamed progress over SSE; a disconnect cancelled the run and deleted the session;
uploads sat in `/tmp/sindri_sessions`; nothing serialised GPU use. Extraction takes
roughly 10-20 minutes per drawing on the GPU host.

The goal (`docs/plans/2026-10-07-batch-async-review-design.md`, "Goal") was an
asynchronous compute capability with human review at a later, uncertain time: import tens
to hundreds of drawings, let them extract with no browser open, review over days, resume
exactly where the reviewer stopped.

Constraints from the operator's decisions (design §0): one person imports and reviews
(#2); exactly one extraction at a time, because the 72B model owns a whole GPU (#4);
in-process worker thread plus SQLite, single uvicorn process (#5); a crash requeues the
running job and a drawing fails after two attempts (#6); hosting undecided, so nothing may
assume an external service (#23).

## Decision

Run extraction on **one daemon worker thread inside the web process**, claiming jobs from
a **SQLite index** (`sindri.db`, WAL, `synchronous=FULL`, numbered append-only migrations
keyed on `PRAGMA user_version`). Blobs stay on disk under `SINDRI_DATA_DIR`; the review
store is keyed by the same document id. The deployment is **exactly one uvicorn process**,
enforced by an exclusive `flock` on a lock file taken in `Worker.start()`; import recovery
and the housekeeping thread (retention, export-ZIP sweep) run only under that lock.

## Alternatives considered

- **Filesystem-only index** and **moving the review journal into SQLite** — the design
  chose "Approach A" (SQLite is the index, blobs on disk, phase-1 review store unchanged)
  over both (design §1). A filesystem index cannot give atomic claim/priority ordering;
  moving the journal into SQLite would have rewritten shipped, tested phase-1 code.
- **`tus` for resumable upload** — rejected for a weak Python server side and a new
  dependency (design §2); a small chunked protocol was built instead (B2).
- **A broker-backed queue (Celery/Redis or similar)** — not discussed in the design
  documents; implied rejected by decision #5 and by the "no external service, hosting
  undecided" constraint. *(Reconstructed, not quoted from a source.)*

## Consequences

Easier: one deployable container; the queue survives power loss; claim is a single
`BEGIN IMMEDIATE` transaction; the repo takes an injectable clock, so queue ordering is testable deterministically.

Accepted costs:

- **No horizontal scale.** A second process or `--workers > 1` would double-run jobs and
  break recovery; the Dockerfile `CMD` carries a comment saying so, and the lock refuses
  the second process rather than trusting the warning.
- **A cancel is cooperative.** A drawing can be removed while its extraction is still
  physically running; the worker treats a vanished row as a cancel and writes nothing back
  (`app/batch/worker.py` docstring).
- **Recovery is destructive by design.** A container restart during an import can delete a
  half-copied drawing; re-running the load is the remedy (H4-H9 note, "Left as designed").
- **ROWID is data.** `document`/`upload` have TEXT keys, so duplicate order and staging
  order depend on implicit rowid; any migration that rebuilds those tables must copy it
  explicitly (comment in `app/batch/db.py`).

## Lessons

- B2's state file records that the import recovery sweep had to be moved *inside* the
  worker's single-process lock: running it before the lock could delete another
  process's in-flight import. The single-process assumption is a correctness property of
  several features, not just a performance choice, which is why it is enforced rather than
  documented.
- Related: [0002](0002-journal-and-sealed-revisions-as-source-of-truth.md) (the review
  record this queue feeds).
