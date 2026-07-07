#!/usr/bin/env bash
# Regenerate a user-local, corrected copy of the system NVIDIA CDI spec.
#
# Why this exists
# ---------------
# podman injects the GPU using /etc/cdi/nvidia.yaml, which pins exact library
# versions (e.g. libnvidia-egl-wayland.so.1.1.19). When the host NVIDIA driver
# or userspace is updated, those files are replaced with newer versions and the
# pinned paths vanish, so `podman run --device nvidia.com/gpu=...` fails with:
#
#     crun: cannot stat `/usr/lib/.../libnvidia-egl-wayland.so.1.1.19`
#
# The correct fix is `sudo nvidia-ctk cdi generate`, but on a shared box without
# sudo we can't touch /etc/cdi. Instead we write a corrected copy here and let
# run-gpu.sh bind-mount it over the system spec inside a `podman unshare`
# namespace (see run-gpu.sh, "rootless CDI overlay").
#
# This script copies the system spec and rewrites every pinned *.so path that no
# longer exists to the newest version of the same library that does. Re-run it
# after any host driver/userspace update (or if run-gpu.sh starts failing on a
# `cannot stat .../lib*.so.*` error again).
#
# Limitation: it only remaps versioned shared objects (*.so.*). A full driver
# version bump also moves non-.so paths (firmware .bin dirs, etc.); those are
# reported as STILL MISSING and genuinely need `sudo nvidia-ctk cdi generate`.
set -euo pipefail

SRC="${CDI_SRC:-/etc/cdi/nvidia.yaml}"
DST="${CDI_OVERLAY:-$HOME/cdi/nvidia.yaml}"

[ -r "$SRC" ] || { echo "!! cannot read system CDI spec: $SRC" >&2; exit 1; }

mkdir -p "$(dirname "$DST")"
cp "$SRC" "$DST"
echo ">> copied $SRC -> $DST"

# Remap every referenced .so whose pinned version no longer exists on the host.
grep -oE 'hostPath: /[^ ]+\.so[^ ]*' "$SRC" | awk '{print $2}' | sort -u | while read -r hp; do
  [ -e "$hp" ] && continue                      # still present, nothing to do
  base="${hp%.so*}.so"                          # strip the version suffix
  repl="$(ls -1 "${base}".* 2>/dev/null | sort -V | tail -n1 || true)"
  if [ -z "$repl" ] || [ ! -e "$repl" ]; then
    echo "!! no host replacement for $(basename "$hp") — leaving pinned path" >&2
    continue
  fi
  echo ">> remap $(basename "$hp") -> $(basename "$repl")"
  pat="${hp//./\\.}"                            # escape dots for the sed pattern
  sed -i "s#${pat}#${repl}#g" "$DST"            # fixes both hostPath and containerPath
done

echo ">> wrote corrected CDI spec: $DST"

# Report anything still missing (non-.so paths we don't remap, e.g. firmware).
still=0
while read -r hp; do
  [ -e "$hp" ] || { echo "!! STILL MISSING (needs sudo nvidia-ctk cdi generate): $hp" >&2; still=1; }
done < <(grep -oE 'hostPath: /[^ ]+' "$DST" | awk '{print $2}' | sort -u)

[ "$still" -eq 0 ] && echo ">> all referenced host paths exist — ready. Run: GPU=nvidia.com/gpu=1 ./run-gpu.sh"
exit 0
