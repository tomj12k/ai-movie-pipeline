#!/usr/bin/env bash
# One-time: install an SSH key on the Spark so nothing else ever needs the
# password. Prompts for the password once (or reads SPARK_PASS from .env).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$REPO_ROOT/.env"
: "${SPARK_HOST:?}" "${SPARK_USER:?}"

KEY="$HOME/.ssh/id_ed25519_studio"
if [[ ! -f "$KEY" ]]; then
  ssh-keygen -t ed25519 -N "" -f "$KEY" -C "studio-pipeline"
fi

if [[ -n "${SPARK_PASS:-}" ]] && command -v sshpass >/dev/null; then
  SSHPASS="$SPARK_PASS" sshpass -e ssh-copy-id -i "$KEY.pub" "${SPARK_USER}@${SPARK_HOST}"
else
  ssh-copy-id -i "$KEY.pub" "${SPARK_USER}@${SPARK_HOST}"
fi

# Pin the key for this host in ~/.ssh/config.
if ! grep -q "Host ${SPARK_HOST}" "$HOME/.ssh/config" 2>/dev/null; then
  cat >> "$HOME/.ssh/config" <<EOF

Host ${SPARK_HOST}
  User ${SPARK_USER}
  IdentityFile ${KEY}
  IdentitiesOnly yes
EOF
  echo "added ${SPARK_HOST} to ~/.ssh/config"
fi

ssh "${SPARK_USER}@${SPARK_HOST}" 'echo "key auth OK on $(hostname)"'
