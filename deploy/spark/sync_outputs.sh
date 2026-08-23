#!/usr/bin/env bash
# Push ComfyUI outputs to the NAS, organised per project.
#
# ComfyUI writes to ~/ai/outputs/pipeline/<project>/... (studio.py sets the
# filename_prefix to "<project>/<shot>"). Each project directory is mirrored
# into Active_Projects/<project>/renders/ on the NAS. Renders never block on
# the 1GbE wire — this runs out-of-band on a 1-minute timer.
set -euo pipefail

SRC="$HOME/ai/outputs/pipeline"
DST="/mnt/synology/projects"

[[ -d "$SRC" ]] || exit 0
mountpoint -q "$DST" || { echo "NAS not mounted; skipping"; exit 0; }

shopt -s nullglob
for dir in "$SRC"/*/; do
  project="$(basename "$dir")"
  mkdir -p "$DST/$project/renders"
  rsync -a --partial "$dir" "$DST/$project/renders/"
done
