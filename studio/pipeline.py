#!/usr/bin/env python3
"""studio pipeline — run the whole reel with visual checkpoints.

Stages: wireframes -> render -> proxies -> qa.
After every step the frames land in <project>/review/index.html (auto-refreshing
contact sheet), a macOS notification fires, and the pipeline waits for Enter
unless --auto. Shot definitions live in <project>/shots.json:

  [{"id": "s1", "wireframe_frame": 20,
    "style_prompt": "...", "motion_prompt": "...",
    "workflow": "krea2_niko_ltx_pipeline"}, ...]
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import studio as st

BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
STAGES = ["wireframes", "render", "proxies", "assemble", "qa"]


def notify(msg):
    subprocess.run(["osascript", "-e",
                    f'display notification "{msg}" with title "Studio Pipeline" sound name "Glass"'],
                   capture_output=True)


def checkpoint(auto, msg):
    notify(msg)
    print(f"\n■ CHECKPOINT — {msg}")
    if not auto:
        input("  review the contact sheet, then press Enter to continue (Ctrl-C aborts)… ")


class Review:
    """Accumulates images per stage and rewrites the contact sheet."""

    def __init__(self, proj: Path, project: str):
        self.dir = proj / "review"
        self.dir.mkdir(exist_ok=True)
        self.state_f = self.dir / "state.json"
        self.state = json.loads(self.state_f.read_text()) if self.state_f.is_file() else {}
        self.project = project
        self.opened = False

    def add(self, stage, label, image: Path):
        dest = self.dir / f"{stage}_{image.name}"
        shutil.copyfile(image, dest)
        self.state.setdefault(stage, [])
        entry = {"label": label, "img": dest.name, "t": time.strftime("%H:%M:%S")}
        self.state[stage] = [e for e in self.state[stage] if e["label"] != label] + [entry]
        self._write()

    def _write(self):
        self.state_f.write_text(json.dumps(self.state, indent=1))
        rows = []
        for stage in STAGES:
            if stage not in self.state:
                continue
            cells = "".join(
                f'<figure><img src="{e["img"]}" loading="lazy">'
                f'<figcaption>{e["label"]} · {e["t"]}</figcaption></figure>'
                for e in self.state[stage])
            rows.append(f"<h2>{stage}</h2><div class=grid>{cells}</div>")
        html = f"""<!doctype html><meta charset="utf-8">
<meta http-equiv="refresh" content="15"><title>{self.project} review</title>
<style>body{{font:14px/1.5 -apple-system,sans-serif;background:#101619;color:#dfe8e6;
margin:0;padding:28px}}h1{{font-size:20px}}h2{{margin:26px 0 8px;color:#4cc4d1;
text-transform:uppercase;font-size:13px;letter-spacing:.1em}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}}
figure{{margin:0;background:#161f23;border-radius:6px;padding:8px}}
img{{width:100%;border-radius:4px;display:block}}
figcaption{{color:#8fa19d;font-size:12px;margin-top:6px}}</style>
<h1>{self.project} — pipeline review <small style="color:#8fa19d">(auto-refreshes)</small></h1>
{''.join(rows)}"""
        (self.dir / "index.html").write_text(html)

    def show(self):
        if not self.opened:
            subprocess.run(["open", str(self.dir / "index.html")], capture_output=True)
            self.opened = True


def load_shots(proj: Path):
    f = proj / "shots.json"
    if not f.is_file():
        sys.exit(f"missing {f} — define your shots first (see studio/pipeline.py docstring)")
    return json.loads(f.read_text())


def stage_wireframes(proj, shots, rv, a):
    script = proj / "_render_wireframes_auto.py"
    frames = {s["id"]: s.get("wireframe_frame") for s in shots if s.get("wireframe_frame")}
    script.write_text(f"""import bpy, os
LAYOUT = {str(proj / 'blender_layout.py')!r}
exec(compile(open(LAYOUT).read(), LAYOUT, "exec"))
for m in bpy.data.materials:
    if m.use_nodes:
        b = m.node_tree.nodes.get("Principled BSDF")
        if b: m.diffuse_color = b.inputs["Base Color"].default_value
sc = bpy.context.scene
sc.render.engine = 'BLENDER_WORKBENCH'
sc.display.shading.light = 'STUDIO'
sc.display.shading.color_type = 'MATERIAL'
sc.render.resolution_x, sc.render.resolution_y = 1280, 720
sc.render.image_settings.file_format = 'PNG'
for name, frame in {frames!r}.items():
    sc.frame_set(frame)
    sc.render.filepath = os.path.join({str(proj)!r}, name + "_wireframe.png")
    bpy.ops.render.render(write_still=True)
""")
    r = subprocess.run([BLENDER, "--background", "--python", str(script)],
                       capture_output=True, text=True)
    if "Error" in r.stderr or r.returncode != 0:
        sys.exit(f"blender failed: {r.stderr[-400:]}")
    for sid in frames:
        rv.add("wireframes", sid, proj / f"{sid}_wireframe.png")
    rv.show()
    checkpoint(a.auto, "Wireframes rendered — check camera alignment per shot")


def _stills(mp4: Path, outdir: Path, base: str):
    outs = []
    for pct in (25, 50, 75):
        out = outdir / f"{base}_{pct}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4),
                        "-vf", f"select=gte(n\\,{pct})", "-frames:v", "1", str(out)],
                       capture_output=True)
        if out.is_file():
            outs.append(out)
    return outs


def stage_render(proj, shots, rv, a):
    renders = proj / "renders"
    for shot in shots:
        sid = shot["id"]
        before = set(renders.glob("*.mp4")) if renders.is_dir() else set()
        # A shot may override its i2i input ("input_image"), e.g. a frame pulled
        # from the previous shot's render for hard character continuity.
        img = proj / shot.get("input_image", f"{sid}_wireframe.png")
        pid = st.submit_workflow(shot.get("workflow", "krea2_niko_ltx_pipeline"),
                                 a.project,
                                 image=img,
                                 style_prompt=shot["style_prompt"],
                                 motion_prompt=shot["motion_prompt"],
                                 seed=shot.get("seed"))
        print(f"  {sid}: rendering ({pid})…")
        while True:
            time.sleep(10)
            try:
                hist = st.http_json(f"{st.COMFY_URL}/history/{pid}", timeout=10)
            except Exception:
                continue
            if pid in hist:
                stt = hist[pid].get("status", {})
                if stt.get("status_str") == "error":
                    print(f"  !! {sid} failed — continuing with the next shot")
                    notify(f"{sid} FAILED — see terminal")
                    break
                if stt.get("completed"):
                    for _ in range(18):   # wait for the 1-min NAS sync
                        new = (set(renders.glob("*.mp4")) - before) if renders.is_dir() else set()
                        if new:
                            mp4 = max(new, key=lambda p: p.stat().st_mtime)
                            for still in _stills(mp4, rv.dir, f"{sid}"):
                                rv.add("render", f"{sid} {still.stem.split('_')[-1]}%", still)
                            break
                        time.sleep(10)
                    rv.show()
                    notify(f"{sid} rendered — stills on the review sheet")
                    break
    checkpoint(a.auto, "All shots rendered — review before proxies/QA")


def stage_proxies(proj, shots, rv, a):
    r = subprocess.run([sys.executable, str(Path(st.__file__)), "sync-local",
                        "--project", a.project], text=True)
    if r.returncode != 0:
        sys.exit("sync-local failed")
    proxdir = Path.home() / "StudioProxies" / a.project / "proxy"
    for p in sorted(proxdir.glob("*.mp4"))[-8:]:
        stills = _stills(p, rv.dir, f"proxy_{p.stem[:20]}")
        if stills:
            rv.add("proxies", p.stem[:28], stills[1] if len(stills) > 1 else stills[0])
    rv.show()
    checkpoint(a.auto, "Proxies built on the Mac SSD")


def stage_assemble(proj, shots, rv, a):
    """Trim each shot to its shots.json frame count and concat a draft reel.
    Clips map to shots in render order (reel_*_00001 -> first shot, etc.)."""
    raw = Path.home() / "StudioProxies" / a.project / "raw"
    ordered = sorted(raw.glob("reel_*.mp4"))
    pairs = []
    for i, shot in enumerate(shots):
        # A shot may pin its clip explicitly ("clip": "reel_ltx_00007_.mp4"),
        # e.g. after a re-render; otherwise clips map positionally.
        clip = raw / shot["clip"] if shot.get("clip") else \
               (ordered[i] if i < len(ordered) else None)
        if clip is None or not clip.is_file():
            print(f"  !! no clip for {shot['id']} — skipping it in the draft")
            continue
        pairs.append((shot, clip))
    segs = []
    for shot, clip in pairs:
        dur = shot.get("trim_frames", 48) / 24.0
        seg = rv.dir / f"seg_{shot['id']}.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip),
                        "-t", f"{dur:.4f}", "-r", "24", "-an",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                        str(seg)], check=True)
        segs.append(seg)
    lst = rv.dir / "concat.txt"
    lst.write_text("".join(f"file '{s}'\n" for s in segs))
    draft = proj / "draft_reel.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", str(draft)], check=True)
    # Put a copy where `studio qa` looks, so QA reviews the assembled film.
    proxdir = Path.home() / "StudioProxies" / a.project / "proxy"
    proxdir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(draft, proxdir / "draft_reel_proxy.mp4")
    for still in _stills(draft, rv.dir, "draft"):
        rv.add("assemble", f"draft {still.stem.split('_')[-1]}%", still)
    rv.show()
    checkpoint(a.auto, f"Draft reel assembled ({len(segs)} shots) — {draft.name}")


def stage_qa(proj, shots, rv, a):
    r = subprocess.run([sys.executable, str(Path(st.__file__)), "qa",
                        "--project", a.project], capture_output=True, text=True)
    print(r.stdout[-1500:])
    verdict = "see qa_report.md"
    for line in (r.stdout or "").splitlines():
        if "SHIP" in line or "FIX" in line:
            verdict = line.strip()[:80]
            break
    notify(f"QA done: {verdict}")
    print(f"\n■ QA report: {proj / 'qa_report.md'}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="studio pipeline", description=__doc__)
    p.add_argument("--project", required=True)
    p.add_argument("--from", dest="start", default="wireframes", choices=STAGES,
                   help="stage to start at (default: wireframes)")
    p.add_argument("--only", choices=STAGES, help="run a single stage")
    p.add_argument("--auto", action="store_true",
                   help="no pauses; you still get notifications + the sheet")
    a = p.parse_args(argv)

    proj = st.projects_root() / a.project
    shots = load_shots(proj)
    rv = Review(proj, a.project)

    todo = [a.only] if a.only else STAGES[STAGES.index(a.start):]
    for stage in todo:
        print(f"\n━━ stage: {stage} ━━")
        {"wireframes": stage_wireframes, "render": stage_render,
         "proxies": stage_proxies, "assemble": stage_assemble,
         "qa": stage_qa}[stage](proj, shots, rv, a)
    notify("Pipeline complete")
    print("\n✓ pipeline complete — review sheet:", rv.dir / "index.html")


if __name__ == "__main__":
    main()
