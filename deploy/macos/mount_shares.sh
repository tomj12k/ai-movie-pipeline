#!/usr/bin/env bash
# Mount the studio SMB shares on the Mac under /Volumes without sudo.
# Uses AppleScript's `mount volume`, which lands shares at
# /Volumes/Active_Projects and /Volumes/Portfolio_Archive — the exact paths
# studio.py's path translation and DaVinci Resolve expect.
#
# Reads credentials from ~/.studio.env (installed by setup_mac.sh, chmod 600).
# Idempotent: already-mounted shares are skipped. Run at login via the
# com.studio.mountshares LaunchAgent.
set -euo pipefail

ENVF="$HOME/.studio.env"
[[ -f "$ENVF" ]] || { echo "missing $ENVF (run deploy/macos/setup_mac.sh)"; exit 1; }
# shellcheck disable=SC1090
source "$ENVF"
: "${NAS_SMB_HOST:?}" "${NAS_USER:?}" "${NAS_PASS:?}"

mount_share() {
  local share="$1"
  if mount | grep -q "/Volumes/${share} "; then
    echo "already mounted: /Volumes/${share}"
    return 0
  fi
  osascript >/dev/null <<EOF
mount volume "smb://${NAS_USER}:${NAS_PASS}@${NAS_SMB_HOST}/${share}"
EOF
  echo "mounted: /Volumes/${share}"
}

mount_share Active_Projects
mount_share Portfolio_Archive
