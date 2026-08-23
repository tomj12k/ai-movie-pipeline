#!/usr/bin/env bash
# Push the Spark deployment pack from the Mac and run the installer remotely.
# Uses SSH key auth (run deploy/spark/install_ssh_key.sh once first).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$REPO_ROOT/.env"
: "${SPARK_HOST:?}" "${SPARK_USER:?}"

DEST="${SPARK_USER}@${SPARK_HOST}"
rsync -a --delete "$REPO_ROOT/deploy/spark/" "$DEST:~/ai/studio/deploy/"
ssh -t "$DEST" 'bash ~/ai/studio/deploy/setup_spark.sh'
