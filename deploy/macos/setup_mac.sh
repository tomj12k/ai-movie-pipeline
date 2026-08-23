#!/usr/bin/env bash
# One-time Mac setup for the editing cockpit.
#   - ~/.studio.env from the repo .env (600)
#   - ffmpeg (proxy transcodes) via Homebrew
#   - SMB share mounts now + at every login (LaunchAgent)
#   - `studio` command on PATH
#   - Resolve local cache directory
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "== 1. credentials =="
[[ -f "$REPO_ROOT/.env" ]] || { echo "create $REPO_ROOT/.env from .env.example first"; exit 1; }
install -m 600 "$REPO_ROOT/.env" "$HOME/.studio.env"
echo "installed ~/.studio.env"

echo "== 2. ffmpeg =="
command -v ffmpeg >/dev/null || brew install ffmpeg

echo "== 3. SMB mounts =="
bash "$REPO_ROOT/deploy/macos/mount_shares.sh"

AGENT="$HOME/Library/LaunchAgents/com.studio.mountshares.plist"
sed "s|__REPO_ROOT__|$REPO_ROOT|" "$REPO_ROOT/deploy/macos/com.studio.mountshares.plist" > "$AGENT"
launchctl unload "$AGENT" 2>/dev/null || true
launchctl load "$AGENT"
echo "LaunchAgent installed"

echo "== 4. studio CLI =="
mkdir -p "$HOME/.local/bin"
ln -sf "$REPO_ROOT/studio/studio.py" "$HOME/.local/bin/studio"
chmod +x "$REPO_ROOT/studio/studio.py"
echo 'ensure ~/.local/bin is on PATH (default in recent shells)'

echo "== 5. Resolve cache dir =="
mkdir -p /Users/Shared/Resolve_Cache "$HOME/StudioProxies"

echo "done — run: studio status"
