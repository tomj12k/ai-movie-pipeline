#!/usr/bin/env bash
# Nightly dump of the shared Resolve project library to the NAS archive.
# Installed with resolve-db-backup.timer (03:00, before the 03:30 model sync).
set -euo pipefail

DST="/mnt/synology/archive/resolve-db-backups"
mountpoint -q /mnt/synology/archive || { echo "NAS not mounted; skipping"; exit 0; }
mkdir -p "$DST"

STAMP="$(date +%Y%m%d)"
docker exec resolve-db pg_dumpall -U resolve | gzip > "$DST/resolve_${STAMP}.sql.gz"

# Keep 30 days of dumps.
find "$DST" -name 'resolve_*.sql.gz' -mtime +30 -delete
echo "backup written: $DST/resolve_${STAMP}.sql.gz"
