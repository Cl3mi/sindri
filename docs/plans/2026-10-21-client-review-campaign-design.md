# Client review campaign: the client grades our ballooned drawings — design

Status: DESIGN, agreed with the operator on 2026-10-21 (brainstorm). Builds on
the HITL capture layer (`2026-10-07-hitl-review-grading-design.md`, phase 1
shipped) and the batch/async review app (B1-B4). Not a replacement for HITL
phases 2-5; its grader is written so phase 3 can reuse it.

## 0. Goal and decisions

We hand the client ~50 drawings that we have already ballooned and extracted.
Their inspectors review them in our web app, hosted by us, correcting values,
deleting wrong balloons, adding missed ones, and moving balloons and anchor
boxes. Every change is logged server-side as it happens. From those logs we
compute how good the pipeline is **as the client judges it**, what they
changed most, and where the largest room for improvement is. Those findings
then become an improvement plan.

| question | decision |
|---|---|
| where the review runs | our hosted Sindri app (docker) on the operator's Hetzner VPS, public domain behind HTTP basic auth (htpasswd) at a TLS reverse proxy |
| exchange format | none needed: the server-side review journal is the record. No client-side JSON export (the operator chose logs over a download; see §7) |
| forcing | free review; no blind audit. The shipped gate on unresolved flagged rows stays |
| meaning of "unchanged" | a per-drawing **Approve** asserts "everything I did not change is correct". Rows on unapproved drawings are never counted as correct |
| position corrections | both, tracked separately: **bubble** (`balloon_xy`, layout) and **anchor** (`target_region`, which feature the balloon points at) |
| drawings | ~50 existing originals that have gold: ~60% that go well, ~40% with known failure sources (`share_select` great/problem). Drawn from train+dev first; test only if short, and then marked burned |
| reasons | optional reason chips on edit and delete (same list as HITL §1) |
| reviewer identity | none, consistent with HITL §0. The app never records the basic-auth user name |
| proposal | frozen: imported from the stored prediction dump, never re-extracted |

Out of scope: blind audit sampling, per-reviewer statistics, a client-facing
quality report, automatic recalibration from the grades, notes, marks and
title-block fields (only characteristics are graded), HITL phases 2-5.

## 1. Deployment (Hetzner VPS)

* **Image:** the existing CPU `Dockerfile` (python-slim + tesseract), not
  `Dockerfile.gpu`. A campaign needs no extraction, so the VPS needs no GPU.
  `OCR_BACKEND=tesseract` serves only `/api/read_region` (manual adds), where
  the reviewer types or corrects the value anyway.
* **Reverse proxy** (Caddy or nginx, the operator's choice) terminates TLS for
  the public domain and enforces basic auth from an htpasswd file. **The
  container port is published on `127.0.0.1` only** (`"127.0.0.1:8000:8000"`).
  Docker's published ports bypass host firewalls such as ufw, so the compose
  default `8000:8000` would expose the app without basic auth.
* `SINDRI_ACCESS_TOKEN` stays unset. The proxy is the gate, and a second
  password prompt adds friction without adding a principal. The B4 cookie's
  missing `Secure` flag does not matter behind TLS.
* `SINDRI_RETENTION_DAYS=0` on the campaign instance. Campaign drawings are
  never exported, so retention would not purge them anyway, but the campaign
  must not depend on that reasoning.
* Single uvicorn worker, as the Dockerfile already requires.
* A compose override `docker-compose.vps.yml` captures the above. Proxy config
  examples live in `docs/deploy/vps.md`, with no secrets in either file.

**This puts client drawings on a third-party host.** Before the first upload
the operator confirms that the client NDA permits hosting at Hetzner (EU), and
turns off Hetzner snapshot backups for the data volume, or accepts that they
hold client data. After the campaign is pulled (§4), the data volume is wiped.
This is a manual checklist in `docs/deploy/vps.md`, not code.

## 2. Campaign builder (operator only)

The builder opens client PDFs and dumps, so under CLAUDE.md §1 it is the
**operator's** tool, like `export_share.py`. An agent selects; the operator
builds.

1. **Selection (agent-safe):** `runner summary --per-doc` digests, then
   `share_select --great 30 --problem 20`, preferring train+dev. The manifest
   is hashes only, as today. `share_select` gets a split-preference flag if it
   lacks one.
2. **Bundle (operator):** `python campaign_build.py --manifest … --name pilot-1
   --out ~/campaign-pilot-1`. For each pick it writes the clean original plus
   `proposal.json` (the dump's `characteristics` + `suggestions`, unmodified)
   and a `campaign.json` listing name, group (great/problem), source run, doc
   hash and the dump's `RunConfig` digest.
3. **Ship (operator):** rsync the bundle to the VPS, then
   `docker compose exec sindri python -m app.campaign.load /import/pilot-1`.
   This creates one **campaign batch** and one document per drawing, in state
   `ready`, with `header.json`/`proposal.json` written exactly as an
   extraction would, plus `campaign`, `group` and `source_run` in the header.
   Loading goes through a CLI inside the container, not a new HTTP route, so
   the public surface does not grow.

`load` is idempotent by drawing sha256 within a campaign and refuses a bundle
whose proposal fails the `ExtractionResult` model.

## 3. Client review flow

Everything not listed here is the shipped review UI.

* **Campaign mode** is a property of the batch. It disables Re-extract, Finish
  & Export, single and mass export, and import for that batch. The work list
  shows the campaign's drawings only.
* **Queue:** the B3 review bar (Prev / Skip / Next), with a "12 / 50 approved"
  counter.
* **Values:** inline edit as today. After an edit or delete commits, the HITL
  reason popover appears. It is optional and dismissed by any other key.
* **Missed balloons:** "+" adds a row via `read_region` (`source: "manual"`).
* **Bubble:** drag, as today (`move_row`).
* **Anchor (new):** the selected row's `target_region` gets move/resize
  handles, and the change is logged as `move_target{id, old, new}`.
  `state.js` and `app/review/replay.py` both gain the op, and the replay
  parity fuzz covers it.
* **Note (new):** one optional free-text note per drawing (`set_note{text}`).
  It is meant for policy statements such as "we never balloon chamfers",
  which the verifier result says gold alone cannot reveal.
* **Approve drawing** replaces Finish in campaign mode. It seals `rN` exactly
  as Finish does, including the gate on unresolved flagged rows, but renders
  no exports. It advances to the next unapproved drawing. Re-opening and
  re-approving seals `rN+1`, and grading uses the latest revision.

The capture transport (debounced `/events`, `sendBeacon`, seq idempotency,
single writer) is unchanged, so the operator can see progress live.

## 4. Pulling the records back

`docker compose exec sindri python -m app.campaign.pull pilot-1 --out
/export/pilot-1.tar` packs the campaign's review dirs (header, proposal,
events, sealed revisions). The operator rsyncs the tar into the protected root
under `reviews/<campaign>/`. **These records are client data under CLAUDE.md
§1.** An agent never reads them; it reads only the counts-only output of §5.

The pull can run at any time, including mid-campaign for a progress snapshot.
The report marks unapproved drawings as such.

## 5. Grading and report

Entry point: `python -m app.eval.runner score --reviewer-grade <campaign dir>`,
a mode of the allowlisted `score`, so **the guard is not widened**. The grade
function itself is pure and lives in product code (`app/review/grading.py`,
no `app.eval` import) so that HITL phase 3 reuses it. The eval side adds the
gold join.

**Per proposal row**, on approved drawings, exactly one value grade:

| grade | meaning |
|---|---|
| `confirmed` | unchanged |
| `edited` | with field signature (`fields:nominal+upper_tol`, namespaced per the digest-key trap) and reason |
| `phantom` | deleted, with delete reason |
| `suggestion_confirmed` / `suggestion_edited` / `suggestion_ignored` | suggestion tray outcomes |

`missed` = rows added by hand, which is a lower bound.

**Placement**, independent of the value grade, in mm at the drawing's scale:
* bubble: share moved, median / p90 displacement, share > 5 mm
* anchor: share changed; IoU(old, new) split into `nudged` (IoU ≥ 0.3) and
  `repointed` (< 0.3); centre displacement median / p90

**Client-judged metrics**, per group (great / problem), and pooled with each
group **reweighted to its share of the eligible corpus**. An unweighted pool
of a 60/40 pick would misstate quality.
* precision = confirmed / (confirmed + edited + phantom)
* delivered precision of unflagged rows only (comparable in spirit to gold's
  0.526 / 0.958)
* flag precision = edited-or-deleted flagged / flagged
* recall lower bound = proposal rows that survived / (survived + missed)
* tray accuracy
* review time per drawing (from event timestamps)

**Against gold**, possible because every drawing has gold. The join uses the
existing matcher on (proposal row ↔ gold) and (sealed row ↔ gold):
* **reviewer reliability:** of the rows gold says we got wrong while
  unflagged (`escaped_error`), how many the client edited, and how many they
  confirmed
* **phantoms the client kept:** rows gold calls false detections that the
  client confirmed. This directly measures the "gold is a strict subset of
  what they inspect" hypothesis behind the verifier closure
* client final vs gold field agreement

**"What they changed most":** one ranked table of correction buckets (each
field signature, each delete reason, missed, bubble moved, anchor repointed)
with count, share of all corrections and the drawings they occur on (a count,
plus salted hashes).

**Reconciliation (asserted, and covered by tests):**
* value grades partition the proposal rows of approved drawings
* replaying the net events on the proposal reproduces the latest sealed
  revision
* group counts equal the campaign manifest
* tray grades partition the suggestions

A drawing failing an identity is reported `ungradable: <identity>`, never
dropped silently. Output: a counts-only digest
`docs/eval/<campaign>-review-summary.json`, plus a human-readable
`runner summary` view of it.

## 6. From findings to the improvement plan

The report's last section maps each correction bucket to its lever and to that
lever's status in CLAUDE.md §2-3:

| bucket | lever | status today |
|---|---|---|
| phantom (any reason), especially "we don't balloon it" | ballooning-policy classifier learned on train gold | open (verifier closed) |
| edited tolerance fields | `render_target` fix + adapter retrain | open, priced under active rules |
| edited `char_type` | read stage (Ø Diameter↔Distance) | open |
| edited nominal | read input / box framing | open |
| anchor repointed / nudged | box framing (`detect_regions`) | open; framing dominates read accuracy |
| bubble moved | whitespace placement (`fa1cb6f`) | new, unmeasured |
| missed | detector weights | open (Rung 3 moved isolated misses) |

The improvement plan is a separate document written after the first campaign
report. Each arm it proposes is registered before it is priced, as CLAUDE.md
§4 requires. Client-judged numbers from the campaign become a third control
alongside `r5-control` and `r5-controltest`.

## 7. Export to the client

Not built. The operator's decision is that server-side logs are the record. If
the client later wants their own copy, a campaign-only "download my review"
route that serialises the sealed revisions is a small, separate addition.

## 8. Testing

TDD per CLAUDE.md §4. Planned test surfaces:
* `move_target` / `set_note`: JS↔Python replay parity fuzz extended; node tests
  for the anchor handles' op emission
* campaign mode: node tests (no export / re-extract controls, Approve gating,
  "next unapproved"), API tests that the export and re-extract routes refuse
  campaign documents
* `app.campaign.load`: synthetic bundle → documents in `ready`, headers carry
  campaign / group / source run; idempotent re-load; malformed proposal refused
* `app.campaign.pull`: round-trip of a synthetic campaign
* `grading.py`: synthetic sessions for every grade, placement maths in mm,
  reweighting, every reconciliation identity (including a deliberately
  inconsistent session → `ungradable`)
* gold join: synthetic gold + proposal + sealed fixtures
* `bash ~/.claude/hooks/test-sindri-guard.sh` still `32 passed, 0 failed`
* deploy: `docker compose -f docker-compose.yml -f docker-compose.vps.yml
  config` shows the port bound to 127.0.0.1

## 9. Build order

1. Campaign mode + Approve (batch flag, UI gating, seal without export)
2. `move_target` + anchor handles, `set_note`, reason popover
3. `campaign_build.py`, `app.campaign.load`, `app.campaign.pull`
4. `grading.py` + `score --reviewer-grade` + gold join + digest
5. VPS compose override + `docs/deploy/vps.md` + operator dry run on a
   synthetic campaign on the VPS
6. Pilot with the client; then the findings report and the improvement plan
