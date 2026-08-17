#!/usr/bin/env bash
# Move the minimum client data needed for a GPU prediction run, and bring back
# only the predictions.
#
# Deliberately narrow. Prediction needs the CLEAN drawings and the split file;
# it does not need the inspection sheets, the gold values, or the ballooned
# drawings. Those never leave the originating machine, so scoring happens where
# the gold lives and the sheets stay put.
#
# Prints counts and byte totals only — never a filename.
#
#   ./sync_client_data.sh push <ssh-host> <remote-root>
#   ./sync_client_data.sh pull <ssh-host> <remote-root> <run-name> <local-root>
#
# Symlinks are dereferenced (-L): corpus/ is a symlink view over the delivery.
set -uo pipefail

LOCAL_ROOT="${SINDRI_CLIENT_ROOT:-$HOME/sindri-client-data}"

usage() { sed -n '2,16p' "$0" >&2; exit 2; }
[ $# -ge 3 ] || usage
ACTION="$1"; HOST="$2"; RROOT="$3"

# Resolve the remote root to an absolute path ONCE. A tilde inside a quoted ssh
# argument is not expanded by the remote shell, so `mkdir -p '~/x'` silently
# creates a directory literally named "~" — while rsync DOES expand it and then
# fails looking for the real path. Resolving here removes the whole class of bug.
RROOT=$(ssh -o BatchMode=yes "$HOST" "eval echo $RROOT") || {
    echo "cannot resolve remote root on $HOST" >&2; exit 1; }
case "$RROOT" in
    /*) ;;
    *) echo "remote root did not resolve to an absolute path: $RROOT" >&2; exit 1 ;;
esac

case "$ACTION" in
push)
    src_pdfs="$LOCAL_ROOT/corpus/originals"
    [ -d "$src_pdfs" ] || { echo "missing: corpus/originals" >&2; exit 1; }
    n=$(find "$src_pdfs" -maxdepth 1 \( -type f -o -type l \) | wc -l)
    bytes=$(du -Lsb "$src_pdfs" | cut -f1)
    echo "pushing $n drawings ($((bytes/1048576)) MiB) -> $HOST:$RROOT"

    ssh -o BatchMode=yes "$HOST" "mkdir -p '$RROOT/corpus/originals' '$RROOT/meta' '$RROOT/runs'" || exit 1
    rsync -aL --delete --info=stats1 "$src_pdfs/" "$HOST:$RROOT/corpus/originals/" \
        | grep -Ei "number of|total transferred" || true
    rsync -aL --info=stats1 "$LOCAL_ROOT/meta/splits.json" "$HOST:$RROOT/meta/" \
        >/dev/null || exit 1

    # If an agent ever runs on that host, the same guard should apply there.
    ssh -o BatchMode=yes "$HOST" \
        "mkdir -p ~/.claude && grep -qxF '$RROOT' ~/.claude/sindri-protected-paths 2>/dev/null || echo '$RROOT' >> ~/.claude/sindri-protected-paths" || true

    remote_n=$(ssh -o BatchMode=yes "$HOST" "find '$RROOT/corpus/originals' -maxdepth 1 -type f | wc -l")
    echo "verified on remote: $remote_n drawings"
    [ "$remote_n" -eq "$n" ] || { echo "COUNT MISMATCH: local $n vs remote $remote_n" >&2; exit 1; }
    echo "sheets, gold values and ballooned drawings were NOT transferred"
    ;;
pull)
    [ $# -ge 5 ] || usage
    RUN="$4"; DEST_ROOT="$5"
    mkdir -p "$DEST_ROOT/runs/$RUN"
    rsync -a --info=stats1 --include='*.pred.json' --exclude='*' \
        "$HOST:$RROOT/runs/$RUN/" "$DEST_ROOT/runs/$RUN/" \
        | grep -Ei "number of|total transferred" || true
    n=$(find "$DEST_ROOT/runs/$RUN" -maxdepth 1 -name '*.pred.json' | wc -l)
    echo "pulled $n prediction dumps"
    ;;
*)
    usage
    ;;
esac
