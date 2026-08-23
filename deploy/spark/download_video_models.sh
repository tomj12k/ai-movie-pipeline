#!/usr/bin/env bash
# Download the studio's video-generation models to the Spark's local NVMe.
# Runs ON the Spark. Idempotent — hf skips completed files; safe to re-run
# after an interruption.
#
# Lanes (all fully local, no API models):
#   LTX-2.3 22B distilled fp8  — primary iteration lane (8-step, Apache 2.0)
#   Wan 2.2 I2V 14B fp8        — photoreal hero-shot lane (high+low noise MoE)
#   HunyuanVideo 1.5 720p I2V  — motion/physics lane (cfg-distilled fp8)
#
# Already on disk (skipped): krea2 raw/turbo fp8, qwen3vl_4b_fp8_scaled,
# qwen_image_vae, wan_2.1_vae (used by Wan 2.2 14B).
#
# Total new download: ~90GB. Placement follows extra_model_paths.yaml:
#   DiTs -> ~/ai/models/unet   encoders -> ~/ai/models/clip   VAEs -> ~/ai/models/vae
#   full checkpoints -> ~/ai/models/checkpoints
set -euo pipefail

HF="$HOME/ai/comfyui/venv/bin/hf"
M="$HOME/ai/models"
export HF_HOME="${HF_HOME:-$M/llm/_hf_cache}"
STAGE="$M/_staging"
mkdir -p "$STAGE" "$M/checkpoints" "$M/unet" "$M/clip" "$M/vae"

dl() {  # dl <repo> <repo_path> <dest_dir>
  local repo="$1" path="$2" dest="$3"
  local base; base="$(basename "$path")"
  if [[ -f "$dest/$base" ]]; then
    echo "skip (exists): $base"
    return 0
  fi
  echo "downloading: $repo :: $path"
  "$HF" download "$repo" "$path" --local-dir "$STAGE/$repo"
  mv "$STAGE/$repo/$path" "$dest/$base"
}

echo "== LTX-2.3 (primary lane) =="
dl Lightricks/LTX-2.3-fp8 ltx-2.3-22b-distilled-fp8.safetensors "$M/checkpoints"

echo "== Wan 2.2 I2V 14B (hero-shot lane) =="
dl Comfy-Org/Wan_2.2_ComfyUI_Repackaged split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors "$M/unet"
dl Comfy-Org/Wan_2.2_ComfyUI_Repackaged split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors "$M/unet"
dl Comfy-Org/Wan_2.2_ComfyUI_Repackaged split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors "$M/clip"

echo "== HunyuanVideo 1.5 720p I2V (motion lane) =="
dl Comfy-Org/HunyuanVideo_1.5_repackaged split_files/diffusion_models/hunyuanvideo1.5_720p_i2v_cfg_distilled_fp8_scaled.safetensors "$M/unet"
# Text encoders + VAE for HYV 1.5: pull whatever fp8/vae files the repackage
# ships (names vary by revision), then flatten into the Comfy folders.
"$HF" download Comfy-Org/HunyuanVideo_1.5_repackaged \
  --include "split_files/text_encoders/*" "split_files/vae/*" \
  --local-dir "$STAGE/Comfy-Org/HunyuanVideo_1.5_repackaged"
find "$STAGE/Comfy-Org/HunyuanVideo_1.5_repackaged/split_files/text_encoders" -name '*.safetensors' \
  -exec sh -c 'for f; do [ -f "$HOME/ai/models/clip/$(basename "$f")" ] || mv "$f" "$HOME/ai/models/clip/"; done' _ {} +
find "$STAGE/Comfy-Org/HunyuanVideo_1.5_repackaged/split_files/vae" -name '*.safetensors' \
  -exec sh -c 'for f; do [ -f "$HOME/ai/models/vae/$(basename "$f")" ] || mv "$f" "$HOME/ai/models/vae/"; done' _ {} +

echo "== done =="
ls -lh "$M/checkpoints" "$M/unet" "$M/clip" "$M/vae"
