#!/usr/bin/env bash
# Mount the studio SMB shares on the Mac — no sudo, no GUI.
#
# Uses mount_smbfs into ~/StudioMounts/<share>. (The AppleScript
# `mount volume` route to /Volumes hangs when no GUI session can answer
# its dialogs, so it is not used.) If a share is already mounted anywhere
# — including /Volumes/<share> via Finder — it is skipped; studio.py
# resolves whichever mount point exists.
#
# Reads credentials from ~/.studio.env (installed by setup_mac.sh, chmod 600).
# Runs at login via the com.studio.mountshares LaunchAgent.
set -euo pipefail

ENVF="$HOME/.studio.env"
[[ -f "$ENVF" ]] || { echo "missing $ENVF (run deploy/macos/setup_mac.sh)"; exit 1; }
# shellcheck disable=SC1090
source "$ENVF"
: "${NAS_SMB_HOST:?}" "${NAS_USER:?}" "${NAS_PASS:?}"

mount_share() {
  local share="$1"
  if mount | grep -q "/${share} "; then
    echo "already mounted: ${share}"
    return 0
  fi
  local mp="$HOME/StudioMounts/${share}"
  mkdir -p "$mp"
  # -o soft: fail fast if the NAS is down instead of wedging Finder/Resolve.
  mount_smbfs -o soft "//${NAS_USER}:${NAS_PASS}@${NAS_SMB_HOST}/${share}" "$mp"
  echo "mounted: $mp"
}

mount_share Active_Projects
mount_share Portfolio_Archive
