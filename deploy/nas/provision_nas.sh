#!/usr/bin/env bash
# Provision the Synology DS423 for the AI movie pipeline.
#
# Creates shares:   AI_Models, Active_Projects, Portfolio_Archive  (on /volume1)
# Enables:          NFSv4.1 service, SSH, per-share NFS rules for the DGX Spark
# Requires:         .env in the repo root (NAS_HOST, NAS_DSM_PORT, NAS_USER, NAS_PASS,
#                   SPARK_IP; optional SPARK_IP2 for a second interface)
#
# Everything runs over the DSM Web API + one SSH call for `synoshare`.
# Idempotent: safe to re-run; existing shares/rules are left as-is or overwritten
# with the same values.
#
# Note: the DS423 (ARM Realtek) has no NVMe cache slots and no Container Manager.
# Heavy caching is done on the clients by design (see README design rules).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$REPO_ROOT/.env"

: "${NAS_HOST:?}" "${NAS_DSM_PORT:?}" "${NAS_USER:?}" "${NAS_PASS:?}" "${SPARK_IP:?}"
SPARK_IP2="${SPARK_IP2:-}"

BASE="https://${NAS_HOST}:${NAS_DSM_PORT}/webapi/entry.cgi"
# DSM serves a self-signed certificate on the LAN, so verification needs a
# pinned CA: export NAS_CA=/path/to/dsm-cert.pem. Skipping verification while
# POSTing the DSM admin password lets anyone who can ARP-spoof the LAN capture
# it, so that path is now opt-in and noisy rather than the default.
if [[ -n "${NAS_CA:-}" ]]; then
  CURL=(curl -s --cacert "$NAS_CA")
elif [[ "${NAS_INSECURE_TLS:-0}" == "1" ]]; then
  echo "WARNING: NAS_INSECURE_TLS=1 — sending the DSM password over an" >&2
  echo "         unverified TLS connection. Set NAS_CA to pin the cert." >&2
  CURL=(curl -sk)
else
  echo "ERROR: set NAS_CA=/path/to/dsm-cert.pem to verify the DSM certificate." >&2
  echo "       Export the cert from DSM > Control Panel > Security > Certificate." >&2
  echo "       To proceed unverified anyway (LAN only): NAS_INSECURE_TLS=1" >&2
  exit 1
fi

# Login POSTs the credentials via stdin (curl -d @-) so the password never
# appears in a URL, process argv, or the DSM access log query string.
api_login() {
  local resp body
  # Quoted heredoc + os.environ: interpolating the credentials into Python
  # source breaks on any password containing a quote (and is injectable).
  body=$(NAS_USER="$NAS_USER" NAS_PASS="$NAS_PASS" python3 - <<'EOF'
from urllib.parse import urlencode
import os
print(urlencode({"api":"SYNO.API.Auth","version":"7","method":"login",
                 "account":os.environ["NAS_USER"],"passwd":os.environ["NAS_PASS"],
                 "session":"StudioProv","format":"sid","enable_syno_token":"yes"}))
EOF
)
  resp=$(printf '%s' "$body" | "${CURL[@]}" -d @- "$BASE")
  SID=$(python3 -c "import sys,json;print(json.loads(sys.argv[1])['data']['sid'])" "$resp")
  TOKEN=$(python3 -c "import sys,json;print(json.loads(sys.argv[1])['data']['synotoken'])" "$resp")
  [[ -n "$SID" && -n "$TOKEN" ]] || { echo "ERROR: DSM login failed: $resp" >&2; exit 1; }
}

# api_post <api> <version> <method> [--data-urlencode k=v ...]
api_post() {
  local api="$1" version="$2" method="$3"; shift 3
  "${CURL[@]}" -H "X-SYNO-TOKEN:${TOKEN}" "${BASE}?_sid=${SID}&SynoToken=${TOKEN}" \
    --data-urlencode "api=${api}" \
    --data-urlencode "version=${version}" \
    --data-urlencode "method=${method}" \
    "$@"
}

require_ok() {  # require_ok <label> <json>
  python3 - "$1" "$2" <<'EOF'
import sys, json
label, raw = sys.argv[1], sys.argv[2]
d = json.loads(raw)
if not d.get("success"):
    print(f"ERROR: {label}: {raw}", file=sys.stderr); sys.exit(1)
print(f"ok: {label}")
EOF
}

echo "== 1. DSM login =="
api_login

echo "== 2. Enable SSH (needed for synoshare share creation) =="
require_ok "enable ssh" "$(api_post SYNO.Core.Terminal 3 set \
  --data-urlencode "enable_ssh=true" \
  --data-urlencode "enable_telnet=false" \
  --data-urlencode "ssh_port=22")"

echo "== 3. Create shares via synoshare (skips ones that already exist) =="
# synoshare --add <name> <desc> <path> <na-users> <rw-users> <ro-users> <browsable 0|1> <adv_privilege 0~7>
# sshpass -e reads SSHPASS from the environment (never argv); the sudo
# password travels over the encrypted channel on stdin.
SSHPASS="$NAS_PASS" sshpass -e ssh -o StrictHostKeyChecking=accept-new "${NAS_USER}@${NAS_HOST}" "
  P=\$(cat); S=/usr/syno/sbin/synoshare
  add() {
    if echo \"\$P\" | sudo -S \$S --get \"\$1\" >/dev/null 2>&1; then
      echo \"share \$1 already exists\"
    else
      echo \"\$P\" | sudo -S \$S --add \"\$1\" \"\$2\" \"/volume1/\$1\" \"\" \"${NAS_USER}\" \"\" 1 0
      echo \"created \$1\"
    fi
  }
  add AI_Models          'Model weights backup and distribution'
  add Active_Projects    'Live film projects: scripts, USD/Blend stages, renders'
  add Portfolio_Archive  'Finished masters and project cold storage'
" <<<"$NAS_PASS"

echo "== 4. Enable NFS service (v4.1) =="
require_ok "enable nfs" "$(api_post SYNO.Core.FileServ.NFS 3 set \
  --data-urlencode "enable_nfs=true" \
  --data-urlencode "enable_nfs_v4=true" \
  --data-urlencode "enabled_minor_ver=1" \
  --data-urlencode "nfs_v4_domain=localdomain" \
  --data-urlencode "read_size=32768" \
  --data-urlencode "write_size=32768" \
  --data-urlencode "unix_pri_enable=true")"

echo "== 5. NFS export rules for the Spark =="
mk_rule() {  # mk_rule <ip>
  printf '{"client":"%s","privilege":"rw","root_squash":"root","async":true,"insecure":false,"crossmnt":false,"security_flavor":{"sys":true,"kerberos":false,"kerberos_integrity":false,"kerberos_privacy":false}}' "$1"
}
RULES="[$(mk_rule "$SPARK_IP")"
[[ -n "$SPARK_IP2" ]] && RULES+=",$(mk_rule "$SPARK_IP2")"
RULES+="]"

for SHARE in AI_Models Active_Projects Portfolio_Archive; do
  require_ok "nfs rule ${SHARE}" "$(api_post SYNO.Core.FileServ.NFS.SharePrivilege 1 save \
    --data-urlencode "share_name=${SHARE}" \
    --data-urlencode "rule=${RULES}")"
done

echo "== 6. Verify =="
SSHPASS="$NAS_PASS" sshpass -e ssh "${NAS_USER}@${NAS_HOST}" \
  'P=$(cat); echo "$P" | sudo -S cat /etc/exports' <<<"$NAS_PASS" 2>/dev/null | grep -E "volume1" || true

echo
echo "Done. Shares are exported over NFS to the Spark and reachable over SMB"
echo "for the Mac (smb://${NAS_HOST}) as user ${NAS_USER}."
