# Review campaign on the Hetzner VPS

Design: `docs/plans/2026-10-21-client-review-campaign-design.md`. Every step
that touches drawings or review records is the **operator's**: they are
client data (CLAUDE.md §1).

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

1. Docker + compose plugin (compose >= 2.24, needed for `ports: !override`),
   and Caddy from its official apt repository.
2. `git clone` this repo to `~/sindri`, then `mkdir -p ~/sindri/data ~/sindri/import ~/sindri/export`.
3. `caddy hash-password` per reviewer account; fill `deploy/Caddyfile.example`
   into `/etc/caddy/Caddyfile`; `sudo systemctl reload caddy`.
4. `cd ~/sindri && SINDRI_VERSION=$(git rev-parse --short HEAD) docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d --build`
5. Check: `curl -s localhost:8000/api/health` shows `"campaign_only": true`;
   `curl -sI https://<domain>/` answers 401 without credentials.

## What campaign-only mode allows

`SINDRI_CAMPAIGN_ONLY=1` permits only: GET/HEAD `/api/health`,
`/api/documents`, `/api/documents/{id}`, `/api/image/{id}`, `/api/batches`,
`/api/batches/{id}`; POST `/api/auth`, `/open`, `/events`, `/seal`,
`/read_region`. Everything else answers 403 (no upload, export, start, delete).

`/api/read_region` (CPU OCR) has no rate limit of its own; the proxy's basic
auth limits it to reviewers. Caddy's `request_body max_size` is 10MB: a seal
POST carries every row of a drawing (under 1 KB per row, so a few hundred rows
is a few hundred KB) and events batches can be large too.

## Run a campaign

1. Agent: selects drawings (`share_select ... --allow-train --great 30 --problem 20`;
   the campaign draws from train + dev digests, never test) and hands you the
   build command.
2. You: `python campaign_build.py --manifest ~/campaign-manifest.json --name pilot-1 --out ~/campaign-pilot-1`
   - It refuses an `--out` inside the protected root or inside any git work
     tree, and prints hashes only.
   - Re-running skips complete entries (`have <hash>`) and names incomplete
     ones, so it is safe to repeat after an interruption.
3. You: `rsync -a ~/campaign-pilot-1/ vps:~/sindri/import/pilot-1/`
4. You: `ssh vps 'cd ~/sindri && docker compose exec sindri python -m app.campaign.load /import/pilot-1'`
   - The loader reports loaded / skipped / requeued. Re-running is safe and
     requeues a drawing stuck in `staged`.
   - Run ONE loader at a time. Never restart or redeploy the container while a
     load is running: the startup sweep can delete a half-copied drawing, which
     then shows as failed. Remedy: remove the failed drawing and re-run the load.
   - Loaded drawings become ready within about 2 s each (the worker polls); no
     restart is needed.
5. Open the domain yourself, review one drawing end to end, then send the
   client the link and their credentials.
6. Progress at any time: the work list, or a pull (below).

## Get the records back

1. `ssh vps 'cd ~/sindri && docker compose exec sindri python -m app.campaign.pull pilot-1 /export/pilot-1.tar'`
   - Safe during live reviewing. It writes atomically (`<out>.partial`, then
     renamed), skips incomplete records with a warning (type only), and
     excludes the writer token. Grading trusts the database's final revision.
2. `rsync vps:~/sindri/export/pilot-1.tar <protected root>/reviews/`
3. Grading: plan C2 (`runner score --reviewer-grade`).

## After the campaign

`docker compose down`, then delete `~/sindri/data`, `~/sindri/import` and
`~/sindri/export` on the VPS once the final pull is safely in the protected root.
