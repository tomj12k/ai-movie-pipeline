#!/usr/bin/env python3
"""studio — unified pipeline CLI for the distributed AI film studio.

Runs on the MacBook Air (edit cockpit) or any Linux node. Standard library only.

Commands:
  status                          network/service health topology
  script    --project P "idea"    Claude (fable-5) writes a 10s shot list -> NAS
  code-gen  --project P           local Spark vLLM writes a Blender layout .py
  watch     --project P           observe NAS for .usd/.blend drops, trigger hook
  render    --workflow W --project P   submit ComfyUI graph to the Spark
  clear                           restart ComfyUI on the Spark (flush memory)
  qa        --project P           Gemini reviews the latest local proxy
  sync-local --project P          pull renders + build H.264 proxies on this SSD

Config: ~/.studio.env (or <repo>/.env). See .env.example.
"""

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------
# Path translation between OS runtime environments (1GbE shared storage).
# The same project directory as seen from each node:
PATH_MAP = {
    "linux":   "/mnt/synology/projects/",    # DGX Spark (NFS)
    "darwin":  "/Volumes/Active_Projects/",  # Mac via Finder…
    "darwin_alt": str(Path.home() / "StudioMounts/Active_Projects"),  # …or headless
}


def load_env():
    cfg = {}
    for f in (Path.home() / ".studio.env",
              Path(__file__).resolve().parent.parent / ".env"):
        if f.is_file():
            for line in f.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg.setdefault(k.strip(), v.strip())
            break
    cfg.update({k: v for k, v in os.environ.items() if k in cfg or k.startswith(("SPARK_", "NAS_", "VLLM_", "COMFYUI_", "RESOLVE_"))})
    return cfg


CFG = load_env()
SPARK = CFG.get("SPARK_HOST", "spark-d1a9.local")
NAS = CFG.get("NAS_HOST", "192.168.68.131")
VLLM_URL = CFG.get("VLLM_URL", f"http://{SPARK}:8000/v1")
COMFY_URL = CFG.get("COMFYUI_URL", f"http://{SPARK}:8188")


def projects_root() -> Path:
    """The Active_Projects share as mounted on THIS machine."""
    sysname = platform.system().lower()
    if sysname == "darwin":
        for p in (Path(PATH_MAP["darwin"]), Path(PATH_MAP["darwin_alt"])):
            if p.is_dir():
                return p
        sys.exit("Active_Projects is not mounted — run deploy/macos/mount_shares.sh")
    if sysname == "linux":
        p = Path(PATH_MAP["linux"])
        if p.is_dir():
            return p
        sys.exit("NFS mount missing — run deploy/spark/setup_spark.sh")
    sys.exit(f"unsupported platform: {sysname}")


def translate(path: Path, target: str) -> str:
    """Re-express a path under Active_Projects for another node's OS."""
    rel = path.resolve().relative_to(projects_root().resolve())
    return str(Path(PATH_MAP[target]) / rel)


def http_json(url, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def run(cmd, **kw):
    return subprocess.run(cmd, text=True, capture_output=True, **kw)


# ------------------------------------------------------------------ status
def probe(host, port, timeout=2.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def cmd_status(_):
    checks = [
        ("DGX Spark",   SPARK, [("ssh", 22), ("vLLM", 8000), ("ComfyUI", 8188),
                                ("Resolve DB", 5432)]),
        ("Synology NAS", NAS,  [("SMB", 445), ("DSM", 5000), ("NFS", 2049)]),
    ]
    print("┌─ studio topology ──────────────────────────────────────┐")
    ok_all = True
    for name, host, ports in checks:
        up = probe(host, ports[0][1])
        print(f"│ {name:<13} {host:<20} {'● up' if up else '○ DOWN'}")
        for label, port in ports:
            good = probe(host, port)
            ok_all &= good
            print(f"│   ├─ {label:<11} :{port:<6} {'✓' if good else '✗ unreachable'}")
    root = None
    try:
        root = projects_root()
    except SystemExit:
        ok_all = False
    print(f"│ Storage        Active_Projects  "
          f"{'✓ ' + str(root) if root else '✗ not mounted'}")
    if probe(SPARK, 8000):
        try:
            models = http_json(f"{VLLM_URL}/models", timeout=5)
            names = ", ".join(m["id"] for m in models.get("data", []))
            print(f"│ LLM            {names}")
        except Exception:
            print("│ LLM            (loading…)")
    print("└────────────────────────────────────────────────────────┘")
    sys.exit(0 if ok_all else 1)


# ------------------------------------------------------------------ script
SHOTLIST_BRIEF = """Write a frame-by-frame cinematic shot list for a seamless \
10-second portfolio reel (24 fps, 240 frames) based on this idea:

{idea}

Structure it as markdown with: title, logline, a table of shots (shot id, \
frame range, duration, camera move on a 35mm lens, subject/action, lighting, \
style keywords), then per-shot image-generation prompts (for Krea 2) and \
video-motion prompts (for LTX-2.3 / Wan 2.2 image-to-video). Keep everything \
physically continuous so shots cut together seamlessly."""


def cmd_script(a):
    proj = projects_root() / a.project
    proj.mkdir(parents=True, exist_ok=True)
    idea = " ".join(a.idea) or input("Shot idea: ")
    print("→ claude (fable-5) is writing the shot list…")
    r = run(["claude", "--model", "claude-fable-5", "-p", SHOTLIST_BRIEF.format(idea=idea)],
            timeout=600)
    if r.returncode != 0:
        sys.exit(f"claude failed: {r.stderr.strip()[:400]}")
    out = proj / "shotlist.md"
    out.write_text(r.stdout)
    print(f"✓ shot list saved: {out}\n  (Spark sees it at "
          f"{translate(out, 'linux')})")


# ---------------------------------------------------------------- code-gen
CODEGEN_PROMPT = """You are a Blender 4.x Python expert. From the shot list \
below, write ONE complete Python script (bpy) that builds the base bounding \
layout for the whole reel: one collection per shot containing named bounding \
boxes for every subject/prop (correct relative scale/position), a ground \
plane, placeholder lights matching each shot's lighting note, and a single \
35mm camera on an animated track that reproduces every camera move across \
frames 1-240 at 24 fps (keyframe the camera per shot's frame range). \
Set scene resolution 1920x1080. The script must run cleanly from Blender's \
Text Editor: never touch bpy.context.space_data, never read \
bpy.context.active_object (use bpy.context.view_layer.objects.active), and \
avoid operators that need a 3D-viewport context — prefer bpy.data.objects.new \
over bpy.ops primitives where practical. Reply with ONLY the Python code.

SHOT LIST:
{shotlist}"""


def cmd_codegen(a):
    proj = projects_root() / a.project
    shotlist = proj / "shotlist.md"
    if not shotlist.is_file():
        sys.exit(f"no shot list at {shotlist} — run: studio script --project {a.project}")
    print("→ Spark vLLM (Qwen3.8-27B) is writing the Blender layout…")
    resp = http_json(f"{VLLM_URL}/chat/completions", {
        "model": CFG.get("VLLM_MODEL", "Qwen3.8-27B"),
        "messages": [{"role": "user",
                      "content": CODEGEN_PROMPT.format(shotlist=shotlist.read_text())}],
        # Reasoning off: Qwen3.x otherwise spends the whole budget thinking
        # aloud before any code appears (~5-9 tok/s on the GB10).
        "chat_template_kwargs": {"enable_thinking": False},
        "max_tokens": 16384, "temperature": 0.2,
    }, timeout=2700)
    code = resp["choices"][0]["message"]["content"]
    code = re.sub(r"<think>.*?</think>", "", code, flags=re.S).strip()
    m = re.search(r"```(?:python)?\n(.*?)```", code, re.S)
    if m:
        code = m.group(1)
    elif code.startswith("```"):
        # Unclosed fence (model hit its token budget or just omitted the
        # closing fence): drop the fence line, keep everything after it.
        code = code.split("\n", 1)[1] if "\n" in code else ""
    out = proj / "blender_layout.py"
    out.write_text(code)
    # compile("") succeeds, so an empty/near-empty generation would otherwise
    # pass validation and hand Blender a script that silently does nothing.
    if len(code.strip()) < 200 or "bpy" not in code:
        sys.exit(f"✗ generated layout looks empty or truncated "
                 f"({len(code.strip())} chars, bpy {'found' if 'bpy' in code else 'MISSING'})"
                 f"; re-run code-gen")
    try:
        compile(code, str(out), "exec")
    except SyntaxError as e:
        sys.exit(f"✗ generated file is not valid Python ({e}); re-run code-gen")
    print(f"✓ Blender layout saved: {out}\n  Open the stage machine and run it "
          f"inside Blender (Scripting tab).")


# ------------------------------------------------------------------- watch
def _stable(f: Path) -> bool:
    """Two identical size samples 500ms apart — clears 1GbE write-lag buffers."""
    try:
        s1 = f.stat().st_size
        time.sleep(0.5)
        s2 = f.stat().st_size
        time.sleep(0.5)
        return s1 == s2 == f.stat().st_size and s1 > 0
    except OSError:
        return False


def cmd_watch(a):
    root = projects_root() / a.project
    root.mkdir(parents=True, exist_ok=True)
    seen = {}
    print(f"👁  watching {root} for .usd/.blend drops (Ctrl-C to stop)")
    while True:
        for f in list(root.rglob("*.usd")) + list(root.rglob("*.usdc")) + \
                 list(root.rglob("*.blend")):
            key = str(f)
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            if seen.get(key) == mtime:
                continue
            if not _stable(f):
                continue           # still copying over the wire — next pass
            seen[key] = f.stat().st_mtime
            print(f"● stable drop: {f.name} — triggering render hook")
            if a.workflow:
                try:
                    submit_workflow(Path(a.workflow), a.project)
                except Exception as e:
                    print(f"  render trigger failed: {e}")
            else:
                print(f"  (no --workflow given; drop registered only)")
        time.sleep(a.interval)


# ------------------------------------------------------------------ render
def upload_image(path: Path) -> str:
    """Multipart POST to ComfyUI /upload/image; returns the server-side name."""
    boundary = "----studioboundary7355608"
    data = path.read_bytes()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: application/octet-stream"
            f"\r\n\r\n").encode() + data + \
           (f"\r\n--{boundary}\r\nContent-Disposition: form-data; "
            f"name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n").encode()
    req = urllib.request.Request(
        f"{COMFY_URL}/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())["name"]


def resolve_workflow(wf: str) -> Path:
    """Accept an absolute path, a repo-relative path, or a bare name —
    from any working directory."""
    repo_workflows = Path(__file__).resolve().parent.parent / "workflows"
    name = Path(wf).name
    candidates = [Path(wf).expanduser(), repo_workflows / name,
                  repo_workflows / f"{name}.json"]
    for c in candidates:
        if c.is_file():
            return c
    sys.exit(f"workflow not found: {wf}\n  available: " +
             ", ".join(p.stem for p in sorted(repo_workflows.glob('*.json'))))


def submit_workflow(wf_path, project: str, image=None,
                    style_prompt=None, motion_prompt=None, seed=None,
                    prefix=None, krea_denoise=None) -> str:
    """prefix names the output files (e.g. 's04_take' -> s04_take_00001_.mp4,
    retakes increment the counter under the same shot name)."""
    raw = json.loads(resolve_workflow(str(wf_path)).read_text())
    graph = {k: v for k, v in raw.items() if isinstance(v, dict)}  # drop _meta_workflow
    img_name = upload_image(Path(image)) if image else None
    for node in graph.values():
        ins = node.get("inputs", {})
        title = node.get("_meta", {}).get("title", "")
        # Route every save node into the project's folder on the Spark.
        if "filename_prefix" in ins:
            base = prefix or Path(str(ins["filename_prefix"])).name
            if prefix and node.get("class_type") == "SaveImage":
                base = f"{prefix}_frame"      # keep style frames distinct
            ins["filename_prefix"] = f"{project}/{base}"
        if img_name and title == "input_wireframe":
            ins["image"] = img_name
        if style_prompt and title == "style_prompt":
            ins["text"] = style_prompt
        if style_prompt and title == "music_tags":
            ins["tags"] = style_prompt
        if motion_prompt and title == "motion_prompt":
            ins["text"] = motion_prompt
        if seed is not None:
            for k in ("seed", "noise_seed"):
                if k in ins:
                    ins[k] = int(seed)
        if krea_denoise is not None and title == "krea2 restyle (i2i)":
            ins["denoise"] = float(krea_denoise)
    resp = http_json(f"{COMFY_URL}/prompt", {"prompt": graph})
    pid = resp["prompt_id"]
    print(f"→ submitted to ComfyUI: {pid}")
    return pid


def cmd_render(a):
    pid = submit_workflow(Path(a.workflow), a.project, image=a.image,
                          style_prompt=a.style_prompt, motion_prompt=a.motion_prompt,
                          seed=a.seed, prefix=a.prefix)
    print("… rendering on the Spark (Ctrl-C detaches; render continues)")
    t0, lost = time.time(), 0
    while True:
        time.sleep(10)
        try:
            hist = http_json(f"{COMFY_URL}/history/{pid}", timeout=10)
        except Exception:
            continue
        if pid not in hist:
            # ComfyUI keeps history in memory; a restart erases the prompt id
            # and this loop would otherwise poll forever.
            try:
                q = http_json(f"{COMFY_URL}/queue", timeout=10)
                queued = any(i[1] == pid for lane in
                             ("queue_running", "queue_pending")
                             for i in q.get(lane, []))
            except Exception:
                queued = True
            lost = 0 if queued else lost + 1
            if lost >= 3:
                sys.exit(f"✗ job {pid} vanished from ComfyUI after "
                         f"{time.time()-t0:.0f}s (server restarted?)")
        if pid in hist:
            st = hist[pid].get("status", {})
            if st.get("status_str") == "error":
                err = [m for m in st.get("messages", []) if m[0] == "execution_error"]
                detail = err[-1][1].get("exception_message", "")[:400] if err else ""
                sys.exit(f"✗ render failed on node "
                         f"{err[-1][1].get('node_type') if err else '?'}: {detail}")
            if st.get("completed"):
                print(f"✓ render finished in {time.time()-t0:.0f}s; frames sync "
                      f"to Active_Projects/{a.project}/renders within ~1 min")
                return
        print(f"  … {time.time()-t0:.0f}s elapsed")


# ------------------------------------------------------------------- clear
def cmd_clear(_):
    user = CFG.get("SPARK_USER", "pizzacat")
    print("→ restarting ComfyUI on the Spark (flushes unified-memory pool)…")
    r = run(["ssh", f"{user}@{SPARK}", "sudo systemctl restart comfyui"])
    if r.returncode != 0:
        sys.exit(f"ssh failed: {r.stderr.strip()[:300]}")
    for _ in range(30):
        time.sleep(2)
        if probe(SPARK, 8188):
            try:
                http_json(f"{COMFY_URL}/system_stats", timeout=3)
                print("✓ ComfyUI is back; memory pool clean")
                return
            except Exception:
                pass
    sys.exit("ComfyUI did not come back within 60s — check: "
             f"ssh {user}@{SPARK} journalctl -u comfyui -n 50")


# ---------------------------------------------------------------------- qa
QA_PROMPT = """You are a film QA supervisor. Review this rendered video for a \
10-second AI-animated reel. Report, with timestamps: layout errors (clipping, \
floating objects, broken silhouettes), lighting breaks (flicker, direction \
jumps between shots), temporal artifacts (morphing, identity drift), and \
continuity breaks between shots. Then give a verdict: SHIP / FIX (with the \
top 3 fixes). Review this video: @{path}"""


def cmd_qa(a):
    proxies = sorted((Path.home() / "StudioProxies" / a.project / "proxy").glob("*.mp4"),
                     key=lambda p: p.stat().st_mtime)
    if not proxies:
        sys.exit(f"no proxies for {a.project} — run: studio sync-local --project {a.project}")
    target = proxies[-1]
    # Antigravity (`agy`) replaced the retired individual-tier gemini CLI auth;
    # fall back to `gemini` for accounts still on the old path.
    tool = "agy" if shutil.which("agy") else "gemini"
    print(f"→ {tool} QA pass on {target.name}…")
    # --sandbox confines agy to terminal-restricted read-only review;
    # skip-permissions only auto-approves inside that sandbox (it must read
    # the video file non-interactively).
    r = run([tool, "--sandbox", "--dangerously-skip-permissions", "-p",
             QA_PROMPT.format(path=target)] if tool == "agy" else
            [tool, "-p", QA_PROMPT.format(path=target)],
            timeout=2400, cwd=target.parent)
    if r.returncode != 0:
        sys.exit(f"{tool} failed: {(r.stderr or r.stdout).strip()[:400]}")
    report = projects_root() / a.project / "qa_report.md"
    report.write_text(f"# QA report — {target.name}\n\n{r.stdout}")
    print(f"✓ QA report: {report}\n")
    print(r.stdout[:1200])


# --------------------------------------------------------------- sync-local
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}


def cmd_sync(a):
    src = projects_root() / a.project / "renders"
    if not src.is_dir():
        sys.exit(f"no renders yet at {src}")
    dest = Path.home() / "StudioProxies" / a.project
    raw, proxy = dest / "raw", dest / "proxy"
    raw.mkdir(parents=True, exist_ok=True)
    proxy.mkdir(parents=True, exist_ok=True)

    print(f"→ pulling {src} → {raw} (background-friendly, resumable)")
    if shutil.which("rsync"):
        r = run(["rsync", "-a", "--partial", f"{src}/", f"{raw}/"])
        if r.returncode != 0:
            sys.exit(f"rsync failed: {r.stderr.strip()[:300]}")
    else:
        shutil.copytree(src, raw, dirs_exist_ok=True)

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg missing — brew install ffmpeg")
    made = 0
    for f in sorted(raw.rglob("*")):
        if f.suffix.lower() not in VIDEO_EXT:
            continue
        out = proxy / (f.stem + "_proxy.mp4")
        if out.exists() and out.stat().st_mtime >= f.stat().st_mtime:
            continue
        print(f"  ⋯ proxy: {f.name}")
        r = run(["ffmpeg", "-y", "-i", str(f), "-vf", "scale=-2:720",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                 "-c:a", "aac", "-b:a", "128k", str(out)])
        if r.returncode != 0:
            print(f"    ffmpeg failed on {f.name}: {r.stderr[-200:]}")
        else:
            made += 1
    print(f"✓ {made} proxies in {proxy}")
    print("  In Resolve: Project Settings → point proxy media at this folder; "
          "cache stays on /Users/Shared/Resolve_Cache (see configs/resolve).")


# -------------------------------------------------------------------- main
def main():
    # argparse.REMAINDER mis-parses flags straight after a subcommand, so the
    # pipeline subcommand forwards its raw argv before argparse ever runs.
    if len(sys.argv) > 1 and sys.argv[1] == "pipeline":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import pipeline
        return pipeline.main(sys.argv[2:])
    p = argparse.ArgumentParser(prog="studio", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(fn=cmd_status)

    s = sub.add_parser("script"); s.set_defaults(fn=cmd_script)
    s.add_argument("--project", required=True)
    s.add_argument("idea", nargs="*", help="one-line concept for the reel")

    s = sub.add_parser("code-gen"); s.set_defaults(fn=cmd_codegen)
    s.add_argument("--project", required=True)

    s = sub.add_parser("watch"); s.set_defaults(fn=cmd_watch)
    s.add_argument("--project", required=True)
    s.add_argument("--workflow", help="ComfyUI API json to fire on each drop")
    s.add_argument("--interval", type=float, default=5.0)

    s = sub.add_parser("render"); s.set_defaults(fn=cmd_render)
    s.add_argument("--workflow", required=True)
    s.add_argument("--project", required=True)
    s.add_argument("--image", help="local still (e.g. Blender wireframe) to upload")
    s.add_argument("--style-prompt", help="override the Krea 2 style prompt")
    s.add_argument("--motion-prompt", help="override the video motion prompt")
    s.add_argument("--seed", type=int, help="override every sampler seed in the graph")
    s.add_argument("--prefix", help="output filename prefix, e.g. s04_take")

    sub.add_parser("clear").set_defaults(fn=cmd_clear)

    s = sub.add_parser("qa"); s.set_defaults(fn=cmd_qa)
    s.add_argument("--project", required=True)

    s = sub.add_parser("sync-local"); s.set_defaults(fn=cmd_sync)
    s.add_argument("--project", required=True)

    s = sub.add_parser("pipeline", help="run all stages with visual checkpoints")
    s.set_defaults(fn=None)  # handled by the early intercept in main()

    a = p.parse_args()
    try:
        a.fn(a)
    except KeyboardInterrupt:
        print("\ninterrupted")


if __name__ == "__main__":
    main()
