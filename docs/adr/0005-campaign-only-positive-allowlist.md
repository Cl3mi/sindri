# 0005. The hosted campaign instance is gated by a positive route allowlist and scoped to campaign batches

Status: Accepted
Date: 2026-10-21 (C1); amended 2026-11-05 (H4)

## Context

The client review campaign (`docs/plans/2026-10-21-client-review-campaign-design.md`)
hosts roughly 50 already-ballooned client drawings on the operator's Hetzner VPS, behind a
TLS reverse proxy with basic auth. That puts NDA-covered drawings on a third-party host,
and the same app code also serves uploads, exports, re-extraction and deletion for regular
use. The public surface must not grow with the app: a route added next year must not
become reachable on the client's host by accident.

Other forces: the proposal must stay frozen (never re-extracted); campaign drawings are
never exported; the container port is published on `127.0.0.1` only because Docker's
published ports bypass ufw; FastAPI's own `/docs`, `/redoc` and `/openapi.json` also leak
the route schema.

## Decision

`SINDRI_CAMPAIGN_ONLY=1` turns on a **positive allowlist** (`app/campaign/policy.py`):
only the exact GET and POST routes a reviewer's browser calls are permitted; everything
else answers 403, including FastAPI's schema and docs viewers. Loading a bundle goes
through a CLI inside the container (`python -m app.campaign.load`), not an HTTP route, so
the public surface does not grow. After H4, the mode also **scopes reads**: ids that are
not campaign documents, batches or sessions answer 404 (middleware), the two list routes
are filtered, and `read_region` is rate-limited per session. A test (`test_policy.py`)
fails until every new `/api` route is classified.

## Alternatives considered

- **A denylist of dangerous routes** — rejected implicitly by the design's wording: a GET
  route added later (an export status, a listing of someone else's data) "must stay
  closed on the public host until someone decides otherwise".
- **A second token on the app** — rejected in design §1: the proxy is the gate, and a
  second password prompt adds friction without adding a principal.
- **A separate, smaller campaign app** — not documented as considered; the campaign
  reuses the review UI and store, so a mode was chosen over a fork. *(Reconstructed.)*
- **Client-side export of the review as JSON** — the operator chose server-side logs over
  a download (design §0, "exchange format").

## Consequences

Easier: the public host's behaviour is a small, reviewable list; a forgotten route fails
closed; the test makes classification a precondition for adding any route.

Accepted costs:

- Every new `/api` route needs an explicit allowlist decision, even for a purely
  internal one.
- The instance serves exactly one client's campaign data; the runbook (`docs/deploy/vps.md`)
  keeps a manual checklist (NDA allows Hetzner, backups off, ufw 22/80/443 only,
  single-purpose volume) that code cannot enforce.
- Rate limiting is per session with a sliding window kept in memory, so it resets on
  restart.
- Rows in the campaign that match a later ordinary import show as duplicates of the
  campaign copy, by the same-SHA rule (left as designed).

## Lessons

- **H4 was needed because the original allowlist only gated routes, not objects.** The
  allowlist says which routes a reviewer may call; it did not say which ids. My reading of
  the H4 note and the runbook (not a quoted incident) is that a non-campaign document id
  on the same volume was still reachable through an allowed route until the scope
  middleware answered 404 for it. "Which routes" and "which objects" are separate questions.
- Deployment traps recorded in the design are part of the decision: `ports: !override`
  needs compose ≥ 2.24 or the list merges and publishes `0.0.0.0:8000`; Docker < 28
  lets L2 neighbours reach `127.0.0.1`-published ports; the VPS override is made the
  default via `.env` so a habitual plain `docker compose up` cannot republish the app.
- Related: [0002](0002-journal-and-sealed-revisions-as-source-of-truth.md) (Approve seals
  the journal; no export on this instance).
