# 0010. Drawings are imported through a purpose-built chunked, resumable upload protocol into draft batches

Status: Accepted
Date: 2026-10-07 (design); B2 completed 2026-10-08

## Context

The batch design (`2026-10-07-batch-async-review-design.md`) targets importing tens to
hundreds of drawings as single files, a folder or a ZIP, from a browser, with no size
limit (decision 1, 29) and resume after a network or server failure — including after the
tab is closed and the same files are re-selected (decision 21). Hosting was undecided, so
the plain-HTTP LAN case had to work, where `crypto.subtle` does not exist. Large-format
drawings make single-shot uploads fragile.

## Decision

A small protocol of our own, in `app/batch/uploads.py`:

- `POST /api/uploads` registers a file (size, mtime, relative path, and a SHA-256
  *fingerprint* of size plus the first and last MiB, because browsers cannot hash
  incrementally). An unfinished upload with the same fingerprint in the same draft is
  resumed.
- `PUT /api/uploads/{id}?offset=N` accepts a chunk **only when `offset == received`** (else
  409 with the true offset), fsynced before the index moves; the server accepts up to 64 MiB per request and the
  browser sends 8 MiB chunks.
- `POST …/complete` is idempotent: it hashes the whole file (duplicate detection), then
  stages one document per PDF, or unpacks a ZIP server-side (PDFs only; absolute paths,
  `..` and symlinks refused; non-PDFs listed as ignored, never written by name).
- Files land in a **draft batch**; `POST /api/batches/{id}/start` is the only way a staged
  drawing is queued. A free-disk reserve (`SINDRI_MIN_FREE_MB`, default 2048) answers 507.
- The browser remembers the draft per file in `localStorage`, not `sessionStorage`, so a
  closed tab resumes (a plan-review correction).

## Alternatives considered

- **`tus`** — rejected in the design: weak Python server side and a new dependency.
- **Single-shot upload (B1's `/api/upload` + `/enqueue`)** — the interim UI used it; Task 12
  removed both routes once the protocol carried every client.
- **Staging as an implicit side effect of upload** — replaced by an explicit staged draft
  with an Include checkbox, because out-of-scope files and duplicates need a human
  decision before they cost GPU time (decisions 10, 19, 26).

## Consequences

Easier: retries are idempotent, a restart loses at most one chunk, and the same protocol
serves a folder, a ZIP and a single file ("one drawing is a batch of one").

Accepted costs:

- **A lot of state to keep consistent.** A per-batch lock closes a marker race during
  completion; lock order is upload → batch → repo. Recovery (inside the worker's lock, see
  [0001](0001-single-process-worker-with-sqlite-index.md)) deletes only drawing dirs marked
  `.importing` that have no row, plus week-old unfinished uploads and empty drafts.
- **Pure-JS SHA-256 fallback** for plain-HTTP deployments without `crypto.subtle`.
- A lost reply on Start needs a client check (`getDocument`) so the click is not repeated.
- A ZIP is an attack surface, so unpacking streams and refuses unsafe paths before any
  write; the code never writes by the archive's own names.

## Lessons

- The plan was **reviewed before execution** and the review changed it materially: C1
  recovery now runs only while holding the single-process lock, `MAX_CHUNK` returns 413, a
  pure-JS hash fallback was added, a ~10 minute time-based retry budget replaced a fixed
  count, and Task 11 was split into UI and route removal.
- Real-browser drive caught what unit tests could not: 10 scenarios (dropped first PUT,
  every PUT failing then resume after reload, duplicate badge, multi-page warning, damaged
  PDF, plain HTTP, 9 MiB resume after the first 8 MiB, a ZIP with a `.txt`) all passed.
- Related: [0011](0011-single-sheet-supported-size-scope-policy.md) (the staging badges come
  from the scope policy).
