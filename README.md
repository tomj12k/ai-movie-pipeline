# AI Movie Pipeline

A distributed local AI + 3D animation studio across a 1GbE LAN.

| Node | Role | Address |
|------|------|---------|
| MacBook Air M3 | Edit cockpit, DaVinci Resolve, studio CLI | (this machine) |
| DGX Spark GB10 (128GB unified) | vLLM (Qwen3.8-27B) + ComfyUI (Krea 2, LTX-2.3, Wan 2.2, HunyuanVideo 1.5) | `spark-d1a9.local` |
| Synology DS423 (4TB) | Shared storage: Active_Projects, AI_Models, Portfolio_Archive | `192.168.68.131` |
| Windows PC (RTX 3090 Ti) | Blender / Unreal staging — deferred, not wired up yet | — |

## Design rules

- **1GbE network:** never stream heavy media over the wire. Models stay hot on Spark NVMe. Edit from local H.264 proxies synced to the Mac SSD. The NAS holds projects, archives, and backup copies of models.
- **Local model lanes:** Krea 2 (style frames) → LTX-2.3 (primary video, Apache 2.0), Wan 2.2 (photoreal hero shots), HunyuanVideo 1.5 (motion/physics lane). All fully local — no API models.
- **Memory guardrail:** the Spark's AI services must leave ≥30GB of the 128GB unified pool free. vLLM is capped accordingly; `studio clear` flushes ComfyUI between model-heavy runs.
- **No secrets in git:** copy `.env.example` to `.env` and fill it in.

## Layout

```
deploy/nas/        Synology provisioning (DSM Web API — no SSH needed)
deploy/spark/      NFS mounts, systemd services, model downloads, Resolve Postgres
deploy/macos/      SMB mounts + LaunchAgent, Mac setup
configs/           CLI endpoint profiles (opencode, claude, codex, gemini) + Resolve
studio/            studio.py — the unified pipeline CLI
workflows/         ComfyUI API-format graphs (Krea2 → LTX / Wan / Hunyuan)
docs/RUNBOOK.md    End-to-end production runbook
```

## Quick start

1. `cp .env.example .env` and fill in credentials.
2. `bash deploy/nas/provision_nas.sh` — creates shares, enables NFS/SMB.
3. `bash deploy/spark/setup_spark.sh` — mounts NFS, installs services, queues model downloads.
4. `bash deploy/macos/setup_mac.sh` — mounts shares, installs ffmpeg, links `studio`.
5. `studio status` — verify the whole topology is green.

See `docs/RUNBOOK.md` for the full 10-second-reel production lifecycle.
