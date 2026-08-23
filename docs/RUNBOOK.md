# Production runbook — a 10-second portfolio reel, end to end

One creator, four machines, one wire. Follow the steps in order; every command
runs on the MacBook Air unless noted.

## 0. Preflight (30 seconds)

```bash
studio status
```

All probes must be ✓. If a NAS mount is missing:
`bash deploy/macos/mount_shares.sh`. If ComfyUI misbehaves: `studio clear`.

## 1. Plan — Claude writes the shot list

```bash
studio script --project neon_hangar "a lone robot powers up inside a derelict starship hangar at dawn"
```

- Claude (fable-5) produces `shotlist.md`: 240 frames of shots, 35mm camera
  moves, per-shot Krea 2 style prompts and LTX/Wan motion prompts.
- The file lands on the NAS: `Active_Projects/neon_hangar/shotlist.md` —
  every machine sees it instantly.

## 2. Stage — the local LLM builds the 3D layout

```bash
studio code-gen --project neon_hangar
```

- The Spark's own Qwen3.8-27B reads the shot list and writes
  `blender_layout.py`: bounding-box geometry per shot, placeholder lights,
  and one 35mm camera keyframed along the full 240-frame track.
- On the staging machine (any box with Blender): open Blender → Scripting →
  run `P:\neon_hangar\blender_layout.py` (Windows) or
  `…/Active_Projects/neon_hangar/blender_layout.py`. Adjust blocking to
  taste, then render a wireframe/viewport still per shot into the project
  folder (and export `.blend`/`.usd` there too).

## 3. Render — Krea 2 styles it, the video model moves it

Hands-off route — the watchdog fires whenever staging drops a new file:

```bash
studio watch --project neon_hangar --workflow workflows/krea2_ltx_pipeline.json
```

(Every `.usd`/`.blend` drop is size-checked twice, 500 ms apart, so half-copied
files over the 1GbE wire never trigger a render.)

Manual route — per shot:

```bash
studio render --workflow workflows/krea2_ltx_pipeline.json \
  --project neon_hangar --image shot01_wireframe.png \
  --style-prompt  "<from shotlist: shot 1 image prompt>" \
  --motion-prompt "<from shotlist: shot 1 motion prompt>"
```

- Lane guide: `krea2_ltx_pipeline` = the 10s @ 24fps workhorse (minutes);
  `krea2_wan_pipeline` = photoreal hero shots (15–25 min each);
  `hunyuan15_i2v_pipeline` = physics-heavy motion.
- Run `studio clear` when switching lanes — it flushes the Spark's unified
  memory pool so the next model family loads clean.
- Finished frames sync themselves to `Active_Projects/<project>/renders/`
  within a minute (Spark timer).

## 4. Sync-local — proxies onto the Air's SSD

```bash
studio sync-local --project neon_hangar
```

Pulls renders from the NAS, then builds 720p H.264 proxies in
`~/StudioProxies/neon_hangar/proxy/`. Editing never touches the network.

## 5. QA — Gemini reviews the pass

```bash
studio qa --project neon_hangar
```

Gemini watches the newest proxy and writes `qa_report.md` next to the shot
list: layout errors, lighting breaks, temporal artifacts, and a SHIP/FIX
verdict. On FIX: adjust prompts or staging, re-render just that shot.

## 6. Master — DaVinci Resolve on the shared library

1. Open Resolve → Project Manager → the `resolve_studio` PostgreSQL library
   (setup: `configs/resolve/RESOLVE_SETUP.md`). Timelines live on the Spark's
   database, so any editing machine resumes the same project.
2. Import from `~/StudioProxies/neon_hangar/raw/`, link proxies, cut the
   10-second reel at 1920×1080/24.
3. Deliver → render the master to
   `Portfolio_Archive/neon_hangar/` (mounted share) — the archive copy and
   the nightly database dump both live on the NAS.

## Recovery quick reference

| Symptom | Fix |
|---------|-----|
| render hangs / OOM | `studio clear` (restarts ComfyUI, keeps 30GB guardrail) |
| LLM down | `ssh pizzacat@spark-d1a9.local sudo systemctl restart vllm-qwen38` |
| NAS share missing on Mac | `bash deploy/macos/mount_shares.sh` |
| NFS missing on Spark | `ssh … sudo mount /mnt/synology/projects` |
| everything | `studio status` tells you which layer broke |
