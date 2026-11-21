# 0002. The review is an event-sourced journal over a server-held proposal, sealed into immutable revisions

Status: Accepted
Date: 2026-10-07

## Context

The client runs Sindri in-house; inspectors review the pipeline's proposal and export
Excel plus a ballooned PDF. The review is also **the measurement instrument**: the HITL
design (`docs/plans/2026-10-07-hitl-review-grading-design.md` §0) wants to grade the
pipeline "as the client's inspectors judge it", and the client review campaign later
depends on it entirely.

Forces:

- Grading needs to know what the system *proposed*, what the inspector *did*, and the
  sealed final. A proposal supplied by the browser could be altered.
- Sessions lived in `/tmp/sindri_sessions` and vanished; a crashed tab lost the review.
- An inspector's performance must not be monitored: no reviewer identity (works-council
  and GDPR concerns, design §0). Retention of every undo would be surveillance-shaped.
- Exports must be reproducible, and a review the server did not record cannot be graded.

## Decision

Model a review as an **append-only journal of reviewer operations** (`events.jsonl`, one
numbered event per `state.js` operation) applied to a **proposal the server wrote at
extraction** (`proposal.json`, never client-supplied), next to a `header.json` that
fingerprints the configuration. **Finish/Approve seals** a revision (`sealed/rN.json`) by
replaying the journal on the server-held proposal; the seal refuses on a sequence gap or
a replay mismatch. Undo is a `retract` event; at seal the journal is compacted to net
operations. **Exports render only from a sealed revision.** Since B1 (design decision 30)
the journal is **mandatory**: there is no "logging off" mode, and an unwritable review
dir fails the boot.

## Alternatives considered

The design table records both rejections (HITL design §0, "architecture"):

- **Final-state annotation** — no journal, no timing, and the proposal would be
  client-supplied.
- **Eval-native scoring** — not shippable to the client and unable to express unsure /
  reasons / audit design.

Also decided in the batch design: the journal stays on disk in the phase-1 store rather
than moving into SQLite.

## Consequences

Easier: exact resume after a crash or on another day (`/open` rebuilds from proposal plus
journal); exports are byte-identical single vs mass because both read the sealed rows;
grading becomes a pure function of `(proposal, sealed revision)`.

Accepted costs:

- **Transport complexity.** Debounced `/events`, `sendBeacon` on close, idempotence by
  `seq`, and a single-writer token (latest tab wins) all exist to keep an incomplete log
  from ever being graded.
- **No full history.** Only net operations are kept after a seal. One exception was added
  later: `scan_read` is LOG-ONLY and never retracted, because an undone scan-rectangle
  read is exactly the attempt that missed and compaction would make every scan look like a
  hit.
- **A journal fault is a visible error, not a degraded mode.** The worker writes
  `result.json` first so a review-store fault is `failed (review_store)` and Retry
  rebuilds the proposal without re-running the GPU.
- **Client-side blinding is UX-level only** (planned for phase 2; not adversarial).
- Review records are client data and are never purged by retention.

## Lessons

- The vocabulary grew after C1 (`move_target`, `set_note`, `flag`, reasons, `scan_read`).
  Every addition had to touch the journal validator, the Python replay, the JS state and
  the parity fuzz together — see [0004](0004-replay-implemented-twice-in-js-and-python.md).
- H6 showed the compaction cost: review time is only a lower bound, so the sealed
  revision gained `raw_timing` and `approve_t_ms` to keep raw timing across seals.
- Grading depends on this record: [0005](0005-campaign-only-positive-allowlist.md) and the
  C2 design (`2026-10-22-campaign-c2-grading-design.md`) both rely on "a replay that does
  not reproduce its seal is ungradable, never graded".
