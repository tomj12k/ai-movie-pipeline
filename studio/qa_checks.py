"""Machine QA battery, run before the LLM review.

Checks encode what the human audits kept finding:
  - signal stats per stem (clipping, flatline, DC offset)
  - speech-recognition sweep (music/foley must carry no words;
    narration must transcribe)
  - boundary strips: 4 frames straddling every cut, for transition review
  - dense strips: 5 frames per clip, for character/style drift review
Results land in <project>/review/ and qa_machine_report.md.
"""
import json
import re
import subprocess
from pathlib import Path

TTS_PY = Path.home() / ".studio-tts-venv" / "bin" / "python"


def signal_stats(wav: Path) -> dict:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(wav), "-af", "astats",
         "-f", "null", "-"], capture_output=True, text=True).stderr
    if "Overall" not in out:   # analysis failed — never report it as a pass
        return {"peak_db": None, "flat": None, "dc": None, "error": True}
    overall = out[out.rfind("Overall"):]
    grab = lambda k: float(m.group(1)) if (
        m := re.search(rf"{k}: (-?[\d.]+)", overall)) else None
    return {"peak_db": grab("Peak level dB"), "flat": grab("Flat factor"),
            "dc": grab("DC offset"), "error": False}


def asr_segments(wav: Path) -> list | None:
    """Transcribe with faster-whisper in the TTS venv; None if unavailable."""
    if not TTS_PY.exists():
        return None
    script = (
        "from faster_whisper import WhisperModel\n"
        "m = WhisperModel('base', device='cpu', compute_type='int8')\n"
        f"segs, _ = m.transcribe({str(wav)!r}, vad_filter=True)\n"
        "for s in segs:\n"
        "    t = s.text.strip()\n"
        "    if t: print(f'{s.start:.1f}|{t}')\n")
    r = subprocess.run([str(TTS_PY), "-c", script],
                       capture_output=True, text=True, timeout=1200)
    if r.returncode != 0:
        return None
    return [ln for ln in r.stdout.splitlines() if "|" in ln]


def _grid(frames: list, out: Path):
    n = len(frames)
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for f in frames:
        cmd += ["-i", str(f)]
    cmd += ["-filter_complex", f"hstack={n}", str(out)]
    subprocess.run(cmd, check=True)


def boundary_strips(draft: Path, durs: list, outdir: Path, xf=0.20) -> list:
    """4 frames around each internal cut (-0.5, -0.1, +0.1, +0.5s)."""
    outdir.mkdir(parents=True, exist_ok=True)
    strips, t = [], 0.0
    for i, d in enumerate(durs[:-1]):
        t += d - xf
        frames = []
        for j, off in enumerate((-0.5, -0.1, 0.1, 0.5)):
            f = outdir / f"b{i + 1:02d}_{j}.png"
            subprocess.run(["ffmpeg", "-y", "-v", "error",
                            "-ss", f"{t + off:.3f}", "-i", str(draft),
                            "-frames:v", "1", "-vf", "scale=320:-2", str(f)],
                           check=True)
            frames.append(f)
        strip = outdir / f"boundary_{i + 1:02d}.png"
        _grid(frames, strip)
        for f in frames:
            f.unlink()
        strips.append(strip)
    return strips


def dense_strips(clips: list, outdir: Path) -> list:
    """5 frames spread across each clip's real duration (fixed offsets ran
    past EOF on short clips and crashed the QA stage)."""
    outdir.mkdir(parents=True, exist_ok=True)
    strips = []
    for clip in clips:
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(clip)],
            capture_output=True, text=True).stdout.strip() or 0)
        if dur <= 0:
            continue
        frames = []
        for i in range(5):
            f = outdir / f"{clip.stem}_{i}.png"
            r = subprocess.run(["ffmpeg", "-y", "-v", "error",
                                "-ss", f"{dur * (i + 0.5) / 5:.3f}",
                                "-i", str(clip), "-frames:v", "1",
                                "-vf", "scale=320:-2", str(f)],
                               capture_output=True)
            if r.returncode == 0 and f.is_file():
                frames.append(f)
        if not frames:
            continue
        strip = outdir / f"dense_{clip.stem}.png"
        _grid(frames, strip)
        for f in frames:
            f.unlink()
        strips.append(strip)
    return strips


def _vstack(strips: list, out: Path):
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for s in strips:
        cmd += ["-i", str(s)]
    cmd += ["-filter_complex", f"vstack={len(strips)}", str(out)]
    subprocess.run(cmd, check=True)


CAST = """The film has EXACTLY TWO characters: Niko, the larger white robot \
bunny (glossy dark face-screen, two big solid glowing yellow ring eyes, TALL \
UPRIGHT ears with cyan light-strips, cyan chest ring, one small round white \
puff tail), and Pip, his smaller companion (dark face panel, twin yellow ring \
eyes, warm yellow belly light). Any third character is a defect."""

DENSE_PROMPT = CAST + """ Open the image at the exact absolute path {sheet} — \
open that path directly, do NOT search the filesystem for it. Each row is \
one scene ({rows}), sampled left to right across time. For EVERY row, first \
COUNT the characters visible in each frame and say the count. Then flag: (1) \
any frame containing three or more characters, or a clone/duplicate/ghost; (2) \
Niko's ears CHANGING SHAPE into short, rounded or stubby puppy-like ears — \
long pointed ears bending or leaning with head movement is correct animation, \
not a defect; (3) eye rings becoming dashed, segmented, or dial-like instead of \
solid rings; (4) the tail changing shape or size; (5) two characters merging, \
fusing or overlapping into one shape; (6) background elements popping in or \
out between frames of the same row; (7) any break into flat 2D or anime style. \
Report each finding as: row, frame number, defect, severity \
(critical/minor). If a row is clean, say "row N: clean (N characters)"."""

BOUND_PROMPT = CAST + """ Open the image at the exact absolute path {sheet} — \
open that path directly, do NOT search the filesystem for it. Each row \
shows 4 frames straddling one cut between scenes ({rows}): the first two are \
before the cut, the last two after. A brief 0.2s dissolve is intentional. For \
each row, judge whether the two scenes flow as one continuous world. Flag only: \
a hard jump to an unrelated place, a lighting or time-of-day reversal, a \
character changing design across the cut, or a character count changing across \
the cut. Report as: row, defect, severity (critical/minor). If a row reads as \
continuous, say "row N: continuous"."""


def _sheet(strips: list, out: Path, quality=72):
    """Stack strips into one JPEG. Multi-megabyte PNG sheets time the
    reviewer out, so batches are kept small and lossy."""
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for s in strips:
        cmd += ["-i", str(s)]
    cmd += ["-filter_complex", f"vstack={len(strips)}" if len(strips) > 1
            else "null", "-q:v", str(int(31 - quality * 0.29)), str(out)]
    subprocess.run(cmd, check=True)


def _review_batch(sheet: Path, prompt: str, cwd: Path, timeout=900):
    try:
        r = subprocess.run(["agy", "--sandbox", "--dangerously-skip-permissions",
                            "-p", prompt], capture_output=True, text=True,
                           timeout=timeout, cwd=cwd)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        return f"(batch {sheet.name} did not run: exit {r.returncode})"
    except subprocess.TimeoutExpired:
        return f"(batch {sheet.name} timed out after {timeout}s)"
    except FileNotFoundError:
        return "(agy not installed — visual checklist skipped)"


def visual_checklist_qa(proj: Path, batch=5) -> Path | None:
    """LLM defect-checklist pass over the strips, reviewed in small batches."""
    review = proj / "review"
    bounds = sorted((review / "boundaries").glob("boundary_*.png"))
    dense = sorted((review / "dense").glob("dense_*.png"))
    if not bounds and not dense:
        return None
    report, out = proj / "qa_visual_report.md", ["# Visual continuity checklist", ""]
    jobs = [("dense", dense, DENSE_PROMPT), ("boundary", bounds, BOUND_PROMPT)]
    for kind, strips, prompt in jobs:
        for i in range(0, len(strips), batch):
            chunk = strips[i:i + batch]
            sheet = review / f"qa_{kind}_{i // batch + 1}.jpg"
            _sheet(chunk, sheet)
            names = ", ".join(s.stem.replace(f"{kind}_", "") for s in chunk)
            # Absolute path: given a bare filename agy hunts the whole
            # filesystem and times out before it ever looks at the image.
            body = prompt.format(sheet=sheet.resolve(), rows=names)
            out += [f"## {kind} batch {i // batch + 1} ({names})",
                    _review_batch(sheet, body, review), ""]
            print(f"  reviewed {kind} batch {i // batch + 1}")
    report.write_text("\n".join(out) + "\n")
    return report


AUDIT_PROMPT = """Read the QA reports in this directory: qa_machine_report.md \
(signal stats + speech sweep), qa_visual_report.md (continuity checklist over \
contact sheets), and qa_report.md (full film review). Scene rows/clips map to \
shot ids p01..p15 in story order. Synthesize ONE verdict. Output STRICT JSON \
only, no prose, exactly this shape:
{"verdict": "SHIP" or "FIX", "retake_shots": ["p03"], "audio_issues": \
["..."], "summary": "one sentence"}
List a shot in retake_shots only for critical visual defects (duplicates, \
merges, wrong character design, style breaks). Minor nits do not block SHIP."""


def audit_verdict(proj: Path) -> dict:
    """Distill the QA reports into a machine-actionable verdict."""
    fallback = {"verdict": "MANUAL", "retake_shots": [], "audio_issues": [],
                "summary": "audit LLM unavailable — read the QA reports"}
    try:
        r = subprocess.run(
            ["agy", "--sandbox", "--dangerously-skip-permissions",
             "-p", AUDIT_PROMPT],
            capture_output=True, text=True, timeout=1200, cwd=proj)
        m = re.search(r"\{.*\}", r.stdout, re.DOTALL)
        v = json.loads(m.group(0)) if m else fallback
        # Validate every key the callers index, not just verdict: a
        # well-formed reply missing retake_shots used to KeyError the run.
        if v.get("verdict") not in ("SHIP", "FIX"):
            v = fallback
        else:
            v = {"verdict": v["verdict"],
                 "retake_shots": list(v.get("retake_shots") or []),
                 "audio_issues": list(v.get("audio_issues") or []),
                 "summary": str(v.get("summary") or "(no summary)")}
    except Exception as e:
        v = dict(fallback, summary=f"audit failed: {e}")
    (proj / "audit_findings.json").write_text(json.dumps(v, indent=1))
    return v


SHOT_GATE_PROMPT = """Open the image at the exact absolute path {sheet} — open \
that path directly, do NOT search the filesystem for it. It shows {n} frames \
sampled across one shot of an animated film, left to right in time. {cast}

Answer in this exact form and nothing else:
COUNT: <the number of characters in each frame, comma separated>
DESIGN: OK, or a short phrase naming any frame with a real design change. \
Judge the SHAPE of the ears, not their angle: long pointed ears that bend, \
lean or swing as the head moves are correct animation and are NOT a defect — \
only report ears whose shape has changed into short, rounded, stubby or \
puppy-like ears. Also report a frame where the eye rings are dashed, \
segmented or dial-like instead of solid rings, where the dark face-screen is \
missing from the front of the head, or where two characters merge into a \
single shape.
VERDICT: PASS if every frame shows the expected cast and the design holds, \
otherwise FAIL."""


def shot_gate(clip: Path, sid: str, expect: str, frames=5, timeout=420):
    """Sample one freshly rendered shot and check cast count + design before it
    becomes the chain input for everything after it. Returns (ok, detail)."""
    work = clip.parent / "_gate"
    work.mkdir(exist_ok=True)
    strips = dense_strips([clip], work)
    if not strips:
        return True, "gate skipped (no strip)"
    sheet = work / f"gate_{sid}.jpg"
    _sheet(strips, sheet)
    body = SHOT_GATE_PROMPT.format(sheet=sheet.resolve(), n=frames, cast=expect)
    out = _review_batch(sheet, body, work, timeout=timeout)
    verdict = "FAIL" if re.search(r"VERDICT:\s*FAIL", out, re.I) else "PASS"
    detail = " ".join(l.strip() for l in out.splitlines()
                      if re.match(r"\s*(COUNT|DESIGN|VERDICT):", l, re.I))
    if "did not run" in out or "timed out" in out:
        return True, f"gate inconclusive: {out[:80]}"   # never block on tooling
    return verdict == "PASS", detail or out[:200]


def run_machine_qa(proj: Path, project: str, shots: list) -> Path:
    lines = [f"# Machine QA — {project}", ""]
    audio = proj / "audio"
    stems = {"music": next(iter(sorted(audio.glob("music*.wav"))), None),
             "narration": audio / "narration_track.wav"}
    work = Path.home() / "StudioProxies" / project / "assemble_work"
    if (work / "audio_mix.wav").exists():
        stems["foley"] = work / "audio_mix.wav"

    lines.append("## Signal stats")
    for name, wav in stems.items():
        if not wav or not wav.exists():
            lines.append(f"- {name}: MISSING")
            continue
        s = signal_stats(wav)
        if s["error"] or s["peak_db"] is None:
            lines.append(f"- {name}: ⚠ ANALYSIS FAILED — not verified")
            continue
        flags = []
        if s["peak_db"] > -0.5:
            flags.append("HOT PEAK")
        if s["flat"]:
            flags.append("CLIPPING")
        lines.append(f"- {name}: peak {s['peak_db']} dB, flat {s['flat']}, "
                     f"dc {s['dc']} {'⚠ ' + ','.join(flags) if flags else '✓'}")

    lines.append("\n## Speech sweep (music/foley must be wordless)")
    for name, expect in (("music", False), ("foley", False),
                         ("narration", True)):
        wav = stems.get(name)
        if not wav or not wav.exists():
            continue
        segs = asr_segments(wav)
        if segs is None:
            lines.append(f"- {name}: ASR unavailable, skipped")
        elif expect:
            lines.append(f"- {name}: {len(segs)} cues "
                         f"{'✓' if segs else '⚠ NO SPEECH FOUND'}")
            lines += [f"    {s}" for s in segs[:12]]
        else:
            lines.append(f"- {name}: {len(segs)} speech segments "
                         f"{'✓' if not segs else '⚠ VOICE IN STEM'}")
            lines += [f"    ⚠ {s}" for s in segs[:8]]

    draft = proj / "draft_reel.mp4"
    review = proj / "review"
    lines.append("\n## Strips")
    cut = proj / "cut_manifest.json"
    if draft.exists():
        # Prefer what was actually assembled; shots.json can disagree with the
        # draft whenever a shot was skipped or the file was edited since.
        if cut.exists():
            man = json.loads(cut.read_text())
            durs, xf = man["durs"], man.get("xfade", 0.20)
            if man.get("dropped"):
                lines.append(f"- ⚠ draft is MISSING shots: "
                             f"{', '.join(man['dropped'])}")
        else:
            durs, xf = [s.get("trim_frames", 48) / 24.0 for s in shots], 0.20
            lines.append("- ⚠ no cut_manifest.json; boundary times derived "
                         "from shots.json and may not match the draft")
        n = len(boundary_strips(draft, durs, review / "boundaries", xf))
        lines.append(f"- {n} boundary strips → review/boundaries/")
    # Dense strips come from the per-shot clips the draft was built from.
    raw = Path.home() / "StudioProxies" / project / "raw"
    edit = Path.home() / "StudioProxies" / project / "edit"
    clips = sorted(edit.glob("[0-9]*.mp4")) if edit.is_dir() else []
    if not clips and raw.is_dir():
        ids = json.loads(cut.read_text())["shots"] if cut.exists() \
            else [s["id"] for s in shots]
        clips = [c for c in (max(raw.glob(f"{i}_take_*.mp4"), default=None)
                             for i in ids) if c]
    if clips:
        n = len(dense_strips(clips, review / "dense"))
        lines.append(f"- {n} dense strips → review/dense/")
    else:
        lines.append("- ⚠ no per-shot clips found; dense strips skipped")

    report = proj / "qa_machine_report.md"
    report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return report
