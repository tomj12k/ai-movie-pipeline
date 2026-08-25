#!/usr/bin/env bash
# Configure the DGX Spark as the studio's AI compute core. Runs ON the Spark
# with sudo rights (pushed + invoked by push_and_install.sh from the Mac).
#
# What it does (all idempotent):
#   1. NFS mounts of the Synology shares (fstab + automount)
#   2. Studio helper scripts -> ~/ai/studio/
#   3. systemd services: vllm-qwen38, comfyui (with the 30GB memory guardrail),
#      sync-outputs (1 min), sync-models (nightly), resolve-db-backup (nightly)
#   4. Migrates the manually-started vLLM/ComfyUI processes to the units
#   5. PostgreSQL 13 container for the shared Resolve project library
#   6. Queues the ~90GB video-model download in the background
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUDIO="$HOME/ai/studio"

echo "== 1. NFS mounts =="
sudo mkdir -p /mnt/synology/models /mnt/synology/projects /mnt/synology/archive
if ! grep -q "/mnt/synology/models" /etc/fstab; then
  sudo tee -a /etc/fstab < "$HERE/fstab.snippet" > /dev/null
  echo "fstab entries appended"
fi
sudo systemctl daemon-reload
for m in models projects archive; do
  mountpoint -q "/mnt/synology/$m" || sudo mount "/mnt/synology/$m"
done
df -h | grep synology

echo "== 2. Studio helper scripts =="
mkdir -p "$STUDIO"
install -m 0755 "$HERE/sync_outputs.sh" "$STUDIO/sync_outputs.sh"
install -m 0755 "$HERE/resolve-db/resolve-db-backup.sh" "$STUDIO/resolve-db-backup.sh"
install -m 0755 "$HERE/download_video_models.sh" "$STUDIO/download_video_models.sh"

echo "== 3. systemd units =="
sudo install -m 0644 "$HERE"/systemd/*.service "$HERE"/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# Scoped passwordless restarts so `studio clear` works unattended over SSH.
sudo bash -c 'printf "%s ALL=(root) NOPASSWD: /usr/bin/systemctl restart comfyui, /usr/bin/systemctl restart vllm-qwen38\n" "${SUDO_USER:-$USER}" > /etc/sudoers.d/90-studio-restart \
  && chmod 440 /etc/sudoers.d/90-studio-restart \
  && visudo -c -f /etc/sudoers.d/90-studio-restart'

echo "== 4. Migrate manual processes to services =="
# Pre-existing USER-level units (Hackster Studio era) must not race the
# system-level ones for port 8188 — disable them first. (Found live: the user
# comfyui.service auto-respawned the old process and the new unit crash-looped
# on 'port already in use'.) embed-server stays user-level untouched.
systemctl --user disable --now comfyui.service comfyui-8189.service vllm-planner.service 2>/dev/null || true
# The long-running manual vLLM and ComfyUI (started from shell scripts) are
# replaced by the units. pkill is scoped to the exact launch commands.
pkill -f "[v]llm serve /home/pizzacat/ai/models/llm" 2>/dev/null && echo "stopped manual vllm" || true
pkill -f "[m]ain.py --disable-pinned-memory" 2>/dev/null && echo "stopped manual comfyui" || true
sleep 3
sudo systemctl enable --now vllm-qwen38.service comfyui.service
sudo systemctl enable --now sync-outputs.timer sync-models.timer resolve-db-backup.timer

echo "== 5. Resolve project-library database (PostgreSQL 13) =="
mkdir -p "$HOME/.config/studio" "$HOME/ai/resolve-db/data"
ENVF="$HOME/.config/studio/resolve-db.env"
if [[ ! -f "$ENVF" ]]; then
  printf 'RESOLVE_DB_PASS=%s\n' "$(openssl rand -hex 16)" > "$ENVF"
  chmod 600 "$ENVF"
  echo "generated $ENVF"
fi
mkdir -p "$STUDIO/resolve-db"
install -m 0644 "$HERE/resolve-db/docker-compose.yml" "$STUDIO/resolve-db/docker-compose.yml"
(cd "$STUDIO/resolve-db" && docker compose --env-file "$ENVF" up -d)

echo "== 6. Video model downloads (background) =="
if ! systemctl --user is-active studio-model-dl.service &>/dev/null; then
  systemd-run --user --unit=studio-model-dl --collect "$STUDIO/download_video_models.sh" \
    && echo "download queued (journalctl --user -u studio-model-dl -f)" \
    || nohup "$STUDIO/download_video_models.sh" > "$HOME/ai/logs/model_dl.log" 2>&1 &
fi

echo "== done. Service state: =="
systemctl --no-pager --plain status vllm-qwen38 comfyui | grep -E "service|Active" || true
