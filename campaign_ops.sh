#!/usr/bin/env bash
# Review-campaign operations: run the campaign instance on THIS machine now,
# move it to the Hetzner VPS later with the same bundle. Operator tool: the
# bundle, the loaded data and the pulled records are client data (CLAUDE.md §1),
# so an agent session refuses to run it.
#
#   ./campaign_ops.sh local-up   [BUNDLE]        load BUNDLE, serve on http://127.0.0.1:8000
#   ./campaign_ops.sh local-pull NAME OUT.tar    pack the local records for grading
#   ./campaign_ops.sh local-wipe                 delete the local campaign data
#
#   ./campaign_ops.sh vps-init   HOST            one-time: dirs, .env, code, docker build+up
#   ./campaign_ops.sh vps-update HOST            ship the current commit and rebuild
#   ./campaign_ops.sh vps-load   HOST NAME BUNDLE  rsync BUNDLE and load it
#   ./campaign_ops.sh vps-pull   HOST NAME DIR     pack on the VPS, fetch into DIR (the protected root's reviews/)
#   ./campaign_ops.sh vps-status HOST
#
# HOST is whatever ssh accepts (an alias from ~/.ssh/config is easiest).
# Caddy, ufw and the reviewer passwords stay manual: docs/deploy/vps.md.
set -euo pipefail

if [[ -n "${CLAUDECODE:-}" ]]; then
    echo "operator tool: run it in your own terminal" >&2
    exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_DIR="${SINDRI_CAMPAIGN_LOCAL:-$HOME/campaign-local}"
PORT="${SINDRI_CAMPAIGN_PORT:-8000}"
REMOTE_DIR='~/sindri'   # expanded by the remote shell

die() { echo "error: $*" >&2; exit 1; }
need() { [[ $# -ge $1 ]] || die "missing argument; see the header of $0"; }

# The same switches the VPS compose override sets, so what you see here is what
# the client will see there. No proxy login locally: bound to 127.0.0.1 only.
campaign_env() {
    export SINDRI_CAMPAIGN_ONLY=1 SINDRI_RETENTION_DAYS=0
    export SINDRI_DATA_DIR="$LOCAL_DIR/data" SINDRI_REVIEW_DIR="$LOCAL_DIR/data/reviews"
    export OCR_BACKEND="${OCR_BACKEND:-tesseract}"
}

local_prep() {
    mkdir -p "$LOCAL_DIR/data" "$LOCAL_DIR/export"
    chmod 700 "$LOCAL_DIR" "$LOCAL_DIR/data" "$LOCAL_DIR/export"
}

cmd_local_up() {
    campaign_env; local_prep
    cd "$HERE"
    if [[ $# -ge 1 ]]; then
        [[ -d "$1" ]] || die "no such bundle: $1"
        python3 -m app.campaign.load "$1"
    fi
    echo "serving the campaign on http://127.0.0.1:$PORT (Ctrl-C stops it; data in $LOCAL_DIR)"
    exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
}

cmd_local_pull() {
    need 2 "$@"; campaign_env; local_prep; cd "$HERE"
    python3 -m app.campaign.pull "$1" "$2"
}

cmd_local_wipe() {
    [[ -d "$LOCAL_DIR" ]] || { echo "nothing at $LOCAL_DIR"; return; }
    read -r -p "delete $LOCAL_DIR (client data)? type yes: " a
    [[ "$a" == yes ]] || die "not deleted"
    rm -rf -- "$LOCAL_DIR"
}

# Ship the COMMITTED tree (no GitHub credentials needed on the VPS, and the
# data/import/export dirs and .env are never touched because git does not
# track them). An uncommitted change is therefore never deployed by accident.
ship_code() {
    local host="$1"
    git -C "$HERE" diff --quiet HEAD -- || echo "note: uncommitted changes are NOT shipped" >&2
    git -C "$HERE" archive --format=tar HEAD | ssh "$host" "mkdir -p $REMOTE_DIR && tar -x -C $REMOTE_DIR"
}

remote_up() {
    local host="$1" sha
    sha="$(git -C "$HERE" rev-parse --short HEAD)"
    ssh "$host" "cd $REMOTE_DIR && SINDRI_VERSION=$sha docker compose up -d --build"
}

cmd_vps_init() {
    need 1 "$@"; local host="$1"
    ssh "$host" "mkdir -p $REMOTE_DIR/data $REMOTE_DIR/import $REMOTE_DIR/export && \
        chmod 700 $REMOTE_DIR/data $REMOTE_DIR/import $REMOTE_DIR/export && \
        { grep -q '^COMPOSE_FILE=' $REMOTE_DIR/.env 2>/dev/null || \
          printf 'COMPOSE_FILE=docker-compose.yml:docker-compose.vps.yml\n' >> $REMOTE_DIR/.env; }"
    ship_code "$host"
    remote_up "$host"
    cmd_vps_status "$host"
    cat <<'EOF'

Still manual (docs/deploy/vps.md, "Install" steps 1 and 3): Docker >= 28,
Caddy + deploy/Caddyfile.example with one `caddy hash-password` per reviewer,
ufw 22/80/443 only. Then, from ANOTHER machine, this must fail to connect:
  curl -m5 http://<server-ip>:8000/api/health
EOF
}

cmd_vps_update() {
    need 1 "$@"
    ship_code "$1"
    echo "do NOT run this while a load is running (docs/deploy/vps.md)." >&2
    remote_up "$1"
}

cmd_vps_load() {
    need 3 "$@"; local host="$1" name="$2" bundle="${3%/}"
    [[ -d "$bundle" ]] || die "no such bundle: $bundle"
    rsync -a -- "$bundle/" "$host:$REMOTE_DIR/import/$name/"
    ssh "$host" "cd $REMOTE_DIR && docker compose exec -T sindri python -m app.campaign.load /import/$name"
    echo "loaded. Open the domain, review one drawing end to end, then send the client the link."
    echo "delete the local bundle when you are done: rm -rf -- $bundle"
}

cmd_vps_pull() {
    need 3 "$@"; local host="$1" name="$2" dest="$3"
    [[ -d "$dest" ]] || die "no such directory: $dest"
    ssh "$host" "cd $REMOTE_DIR && docker compose exec -T sindri python -m app.campaign.pull $name /export/$name.tar"
    rsync -- "$host:$REMOTE_DIR/export/$name.tar" "$dest/"
    echo "pulled to $dest/$name.tar -- an agent can now run score --reviewer-grade on it (docs/deploy/vps.md)."
}

cmd_vps_status() {
    need 1 "$@"
    ssh "$1" "cd $REMOTE_DIR && docker compose ps && curl -s localhost:8000/api/health"
    echo
}

sub="${1:-}"; [[ -n "$sub" ]] || { sed -n '2,20p' "$0"; exit 2; }
shift
case "$sub" in
    local-up) cmd_local_up "$@" ;;
    local-pull) cmd_local_pull "$@" ;;
    local-wipe) cmd_local_wipe ;;
    vps-init) cmd_vps_init "$@" ;;
    vps-update) cmd_vps_update "$@" ;;
    vps-load) cmd_vps_load "$@" ;;
    vps-pull) cmd_vps_pull "$@" ;;
    vps-status) cmd_vps_status "$@" ;;
    *) die "unknown subcommand: $sub" ;;
esac
