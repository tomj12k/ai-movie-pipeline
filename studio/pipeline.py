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


def newest_take(renders: Path, sid: str):
    takes = sorted(renders.glob(f"{sid}_take_*.mp4")) if renders.is_dir() else []
    return takes[-1] if takes else None


def valid_clip(p: Path, min_dur=9.0) -> bool:
    """The NAS sync can copy a clip mid-write; only trust a clip whose
    container probes complete and near full length."""
    if p is None or not p.is_file():
        return False
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip()) >= min_dur
    except ValueError:
        return False


def wait_valid_take(renders: Path, sid: str, tries=24, delay=10):
    for _ in range(tries):
        p = newest_take(renders, sid)
        if valid_clip(p):
            return p
        time.sleep(delay)
    return None


def stage_render(proj, shots, rv, a):
    renders = proj / "renders"
    prev_mp4 = None
    only = set(getattr(a, "shots", "").split(",")) - {""} if getattr(a, "shots", None) else None
    for idx, shot in enumerate(shots):
        sid = shot["id"]
        skip = (getattr(a, "resume", False) and valid_clip(newest_take(renders, sid))) \
               or (only is not None and sid not in only)
        if skip:
            t = newest_take(renders, sid)
            if valid_clip(t):
                prev_mp4 = t          # keep the chain intact across skips
            print(f"  {sid}: skipped")
            continue
        src = shot.get("input_image", f"{sid}_wireframe.png")
        if src == "chain":
            # World-continuity chaining: this shot starts from the final frame
            # of the previous shot's clip, so locations flow instead of jumping.
            if not valid_clip(prev_mp4) and idx > 0:
                prev_mp4 = wait_valid_take(renders, shots[idx - 1]["id"])
            if not valid_clip(prev_mp4):
                print(f"  !! {sid}: no complete previous clip to chain from — skipping")
                continue
            img = proj / f"chain_{sid}.png"
            ok = False
            for _ in range(6):
                r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.15",
                                    "-i", str(prev_mp4), "-frames:v", "1", str(img)],
                                   capture_output=True)
                if r.returncode == 0 and img.is_file():
                    ok = True
                    break
                time.sleep(15)
            if not ok:
                print(f"  !! {sid}: chain frame extraction kept failing — skipping")
                continue
        else:
            img = proj / src
        existing = newest_take(renders, sid)
        pid = st.submit_workflow(shot.get("workflow", "krea2_niko_ltx_pipeline"),
                                 a.project,
                                 image=img,
                                 style_prompt=shot["style_prompt"],
                                 motion_prompt=shot["motion_prompt"],
                                 seed=shot.get("seed"),
                                 prefix=f"{sid}_take",
                                 krea_denoise=shot.get("krea_denoise"))
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
                        mp4 = newest_take(renders, sid)
                        if mp4 and mp4 != existing and valid_clip(mp4):
                            prev_mp4 = mp4
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
        # Resolution order: explicit "clip" pin > newest <sid>_take_*.mp4 >
        # positional legacy (reel_* numbering).
        if shot.get("clip"):
            clip = raw / shot["clip"]
        else:
            clip = newest_take(raw, shot["id"]) or \
                   (ordered[i] if i < len(ordered) else None)
        if clip is None or not clip.is_file():
            print(f"  !! no clip for {shot['id']} — skipping it in the draft")
            continue
        pairs.append((shot, clip))
    # Video: hard cuts on the light events. Audio: every shot generates its own
    # sound world, so raw concat resets tone/level at each cut — instead each
    # segment's audio is loudness-normalized, runs XF seconds past its picture
    # cut, and crossfades into the next shot (J-cut), with fades at both ends.
    XF = 0.20
    work = Path.home() / "StudioProxies" / a.project / "assemble_work"
    work.mkdir(parents=True, exist_ok=True)
    segs, auds, durs = [], [], []
    for shot, clip in pairs:
        dur = shot.get("trim_frames", 48) / 24.0
        seg = work / f"seg_{shot['id']}.mp4"
        frames = int(shot.get("trim_frames", 48))
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip),
                        "-vf", "fps=24", "-frames:v", str(frames), "-an",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                        str(seg)], check=True)
        aud = work / f"aud_{shot['id']}.wav"
        has_audio = bool(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(clip)],
            capture_output=True, text=True).stdout.strip())
        src = ["-i", str(clip)] if has_audio else \
              ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        # Two passes: this ffmpeg's loudnorm truncates downstream filters'
        # EOF (~90ms short), so normalize first, then pad against a silence
        # bed in a clean graph for an exactly video-length segment.
        aud_ln = work / f"ln_{shot['id']}.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error"] + src +
                       ["-vn", "-t", f"{dur:.4f}",
                        "-af", "loudnorm=I=-18:TP=-1.5:LRA=9",
                        "-ar", "48000", "-ac", "2", str(aud_ln)], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(aud_ln),
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                        "-filter_complex",
                        f"[1:a]atrim=0:{dur:.4f}[s];[0:a][s]amix=inputs=2:duration=longest:normalize=0[out]",
                        "-map", "[out]", "-t", f"{dur:.4f}", str(aud)], check=True)
        segs.append(seg); auds.append(aud); durs.append(dur)

    # Video: 0.2s crossfade at every boundary. Chained shots start where the
    # previous frame ended, so the dissolve reads as continuous motion.
    XFV = 0.20
    vid_only = work / "video_only.mp4"
    if len(segs) == 1:
        shutil.copyfile(segs[0], vid_only)
        total = durs[0]
    else:
        fc, cur, t = [], "[0:v]", 0.0
        for i in range(1, len(segs)):
            t += durs[i - 1] - XFV
            out = f"[v{i}]"
            fc.append(f"{cur}[{i}:v]xfade=transition=fade:duration={XFV}:offset={t:.4f}{out}")
            cur = out
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for s_ in segs:
            cmd += ["-i", str(s_)]
        cmd += ["-filter_complex", ";".join(fc), "-map", cur,
                "-c:v", "libx264", "-preset", "fast", "-crf", "18", str(vid_only)]
        subprocess.run(cmd, check=True)
        total = sum(durs) - XFV * (len(segs) - 1)

    # Audio: matching acrossfade chain so both timelines stay locked.
    mixed = work / "audio_mix.wav"
    if len(auds) == 1:
        shutil.copyfile(auds[0], mixed)
    else:
        afc, acur = [], "[0:a]"
        for i in range(1, len(auds)):
            aout = f"[x{i}]"
            afc.append(f"{acur}[{i}:a]acrossfade=d={XFV}:c1=tri:c2=tri{aout}")
            acur = aout
        afc.append(f"{acur}afade=t=in:d=0.2,afade=t=out:st={total - 0.4:.3f}:d=0.4[aout]")
        acmd = ["ffmpeg", "-y", "-v", "error"]
        for af in auds:
            acmd += ["-i", str(af)]
        acmd += ["-filter_complex", ";".join(afc), "-map", "[aout]",
                 "-t", f"{total:.4f}", str(mixed)]
        subprocess.run(acmd, check=True)

    draft = proj / "draft_reel.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(vid_only),
                    "-i", str(mixed), "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-shortest", str(draft)], check=True)
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
    p.add_argument("--resume", action="store_true",
                   help="skip shots that already have a complete take")
    p.add_argument("--shots", help="retake only these shot ids, e.g. s07,s08,s09")
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
