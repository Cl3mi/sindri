# Review campaign on the Hetzner VPS

Design: `docs/plans/2026-10-21-client-review-campaign-design.md`. Every step
that touches drawings or review records is the **operator's**: they are
client data (CLAUDE.md §1).

## No VPS yet: run it locally, move it later

`./campaign_ops.sh` (operator-only; refuses an agent session) wraps every
step below with the same bundle on both sides:

- Now: `./campaign_ops.sh local-up ~/campaign-pilot-1` loads the bundle into
  `~/campaign-local` (outside the repo, mode 700) and serves the review app
  on `http://127.0.0.1:8000` in the SAME campaign-only mode the VPS uses.
  Review there yourself; `local-pull pilot-1 <out.tar>` packs the records for
  grading, `local-wipe` deletes everything.
- The client cannot reach a localhost server, so a local run is for your own
  walk-through and dry run. Reviewing on a different machine needs the VPS.
- When the VPS exists: finish the checklist and "Install" steps 1 and 3
  (Docker, Caddy, ufw, passwords), then
  `./campaign_ops.sh vps-init <host>`, then
  `./campaign_ops.sh vps-load <host> pilot-1 ~/campaign-pilot-1`. Later code
  changes: `vps-update <host>`; records: `vps-pull <host> pilot-1 <reviews dir>`.
  The bundle is the same one: rebuild nothing.

## Before the first upload (manual checklist)

- [ ] The client NDA allows hosting their drawings at Hetzner (EU).
- [ ] Hetzner snapshot backups are OFF for this server, or you accept that
      they hold client data until they expire.
- [ ] ufw allows 22, 80 and 443 only. The app itself is bound to 127.0.0.1.
- [ ] This instance holds ONE client's campaign data and nothing else. The
      campaign-only mode allows reads of documents, batches, images and open
      that are NOT scoped to campaign batches, so anything else loaded here
      is visible to every reviewer.

## Install

1. Docker Engine >= 28 (older engines let L2 neighbours reach
   127.0.0.1-published ports) + compose plugin >= 2.24 (needed for
   `ports: !override`), and Caddy from its official apt repository.
2. `git clone` this repo to `~/sindri`, then `mkdir -p ~/sindri/data ~/sindri/import ~/sindri/export` and
   `chmod 700 ~/sindri/data ~/sindri/import ~/sindri/export`. Make the VPS
   override the default, so a habitual plain `docker compose up` cannot
   re-publish 0.0.0.0:8000 past ufw, unauthenticated and not campaign-only:
   `printf 'COMPOSE_FILE=docker-compose.yml:docker-compose.vps.yml\n' >> ~/sindri/.env`.
3. `caddy hash-password` per reviewer account; fill `deploy/Caddyfile.example`
   into `/etc/caddy/Caddyfile`; `sudo systemctl reload caddy`.
4. `cd ~/sindri && SINDRI_VERSION=$(git rev-parse --short HEAD) docker compose up -d --build`
5. Check: `curl -s localhost:8000/api/health` shows `"campaign_only": true`;
   `curl -sI https://<domain>/` answers 401 without credentials; from ANOTHER machine
   `curl -m5 http://<server-ip>:8000/api/health` must fail to connect.

## What campaign-only mode allows

`SINDRI_CAMPAIGN_ONLY=1` permits only: GET/HEAD `/api/health`,
`/api/documents`, `/api/documents/{id}`, `/api/image/{id}`, `/api/batches`,
`/api/batches/{id}`; POST `/api/auth`, `/open`, `/events`, `/seal`,
`/read_region`. Everything else answers 403 (no upload, export, start, delete),
including FastAPI's `/docs`, `/redoc` and `/openapi.json`.

`/api/read_region` (CPU OCR) has no rate limit of its own; the proxy's basic
auth limits it to reviewers. Caddy's `request_body max_size` is 10MB: a seal
POST carries every row of a drawing (under 1 KB per row, so a few hundred rows
is a few hundred KB) and events batches can be large too.

## Run a campaign

1. Agent: selects drawings and hands you the build command. The great /
   problem labels must reflect TODAY's policy, since that is what the bundle
   shows (plan C1 decision 4), so the digests are:
   - **train**: `r3-trainpredict` was predicted under an older policy, so it is
     first re-scored as today's code would deliver it --
     `python3 -m app.eval.runner score --run <protected root>/runs/r3-trainpredict --gold <protected root>/gold --pdfs <protected root>/corpus/originals --max-pages 1 --dpi 300 --exclude-clamped --splits <protected root>/meta/splits.json --split train --weights docs/eval/weights.json --name reapply-trainpredict-scoped --out <protected root>/reports/reapply-trainpredict-scoped.report.json --reapply-policy`
     -- then `python3 -m app.eval.runner summary <protected root>/reports/reapply-trainpredict-scoped.report.json --per-doc --out <scratchpad>/train.json`.
   - **dev**: `r5-control-scoped` (native, today's policy) --
     `python3 -m app.eval.runner summary <protected root>/reports/r5-control-scoped.report.json --per-doc --out <scratchpad>/dev.json`.
   - then `python -m app.eval.share_select --digest r3-trainpredict=<scratchpad>/train.json --digest r5-control=<scratchpad>/dev.json --allow-train --great 30 --problem 20 --out ~/campaign-manifest.json`.
     Under `--allow-train` it refuses any digest whose split is not train or
     dev: test stays held out. The manifest holds salted hashes and roles
     only -- no client value -- but keep it out of git anyway.
2. You: `python campaign_build.py --manifest ~/campaign-manifest.json --name pilot-1 --out ~/campaign-pilot-1`
   - It refuses an `--out` inside the protected root or inside any git work
     tree, and prints hashes only.
   - Re-running skips complete entries (`have <hash>`) and names incomplete
     ones, so it is safe to repeat after an interruption.
3. You: `rsync -a ~/campaign-pilot-1/ vps:~/sindri/import/pilot-1/`
4. You: `ssh vps 'cd ~/sindri && docker compose exec sindri python -m app.campaign.load /import/pilot-1'`
   - The loader reports loaded / skipped / requeued. Re-running is safe: it
     requeues a drawing stuck in `staged`, and restores the files of a
     `failed` or `cancelled` one and re-queues it.
   - Run ONE loader at a time. Never restart or redeploy the container while a
     load is running: the startup sweep can delete a half-copied drawing, which
     then shows as failed. Remedy: re-run the load; it re-queues failed drawings.
   - Loaded drawings become ready within about 2 s each (the worker polls); no
     restart is needed.
   - Once the load reports success, delete the local bundle
     (`rm -rf ~/campaign-<name>`): it is client data and can be rebuilt from
     the manifest, which holds hashes only.
5. Open the domain yourself, review one drawing end to end, then send the
   client the link and their credentials.
6. Progress at any time: the work list, or a pull (below).

## Get the records back

1. `ssh vps 'cd ~/sindri && docker compose exec sindri python -m app.campaign.pull pilot-1 /export/pilot-1.tar'`
   - Safe during live reviewing. It writes atomically (`<out>.partial`, then
     renamed), skips incomplete records with a warning (type only), and
     excludes the writer token. Grading trusts the database's final revision.
2. You: `rsync vps:~/sindri/export/pilot-1.tar <protected root>/reviews/`
3. Agent (or you): grade it, reading the tar in place --
   `python3 -m app.eval.runner score --reviewer-grade <protected root>/reviews/pilot-1.tar --gold <protected root>/gold --manifest ~/campaign-manifest.json --name pilot-1 --out <protected root>/reports/pilot-1.grade.json --grade-out docs/eval/pilot-1-grade.json`
   - `--gold` must be the gold ingested with `--originals` (the campaign
     showed the clean original; stamped-sheet gold of the same page size
     passes the frame check with every position wrong). `--manifest` is the
     one from step 1 of "Run a campaign": it carries the roles and the
     selection pool the pooled metrics are reweighted to.
   - This is the agent-safe step: stdout is one count line and the digest
     is counts, salted hashes and fixed words. The full report holds client
     values and notes, so it is refused anywhere but inside the protected
     root, under a `.grade.json` name.
   - The count line carries `gold=<joined>/<approved>`, plus
     `gold_not_computed=<reason>:<n>,...` (closed words such as `no_gold`,
     `frame_mismatch`) when any approved drawing was not cross-checked. A
     partial join is normal (not every pick need have gold) and exits 0.
   - Exit 0 = clean; 3 = written but does not reconcile (the digest's
     `reconcile_problems` say which check failed, and a `PROBLEMS <n>` line
     follows the count line; do not quote its numbers until that is
     understood); 1 = refused or failed (if only the digest write failed,
     the report was written); 2 = bad arguments.
   - If NO approved drawing joined gold, the run exits 3 with
     `gold_none_joined` and prints `reviewer-grade: no drawing joined to
     gold (wrong salt, or --gold is not the --originals ingest)`: check
     that `~/.claude/sindri-doc-salt` is the one the campaign was built
     with, and that `--gold` is the `--originals` ingest.
   - Re-running after a later pull regenerates both files in place: an
     existing file is only replaced when it carries its own kind marker
     (`campaign-grade-report` / `campaign-grade-digest`); any other file at
     either path is refused, never overwritten.
   - Commit the digest; never the report or the tar.
4. You, optionally: browse the corrections with crops of the original
   drawings, from your own terminal --
   `python3 -m app.eval.runner campaign-deck --report <protected root>/reports/pilot-1.grade.json --pdfs <protected root>/corpus/originals`
   It serves client values on localhost behind a session token. An agent
   never runs it and never receives the link it prints.

## After the campaign

`docker compose down`, then
`sudo rm -rf ~/sindri/data ~/sindri/import ~/sindri/export` on the VPS (the
container runs as root) once the final pull is safely in the protected root,
and `docker builder prune -af` to drop build cache.
