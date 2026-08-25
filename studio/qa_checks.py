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


CHECKLIST = """You are a continuity supervisor for a 3D animated film about \
Niko (small white robot bunny: dark face-screen with yellow ring eyes, cyan \
ear light-strips, cyan chest ring, ONE small round white puff tail) and Pip \
(palm-sized white sphere drone: two ear-fins, twin yellow ring eyes, warm \
yellow belly light). Review the two contact sheets in this directory: \
dense_sheet.png (5 frames per scene, one scene per row, in story order) and \
boundaries_sheet.png (4 frames straddling each cut between scenes). Hunt \
specifically for: (1) duplicate characters or extra glows/lights; (2) Niko's \
tail changing shape, size, or type between frames or scenes; (3) characters \
merging, fusing, or overlapping into one shape; (4) background elements \
(flowers, trees, props) vanishing or appearing between frames of one scene; \
(5) reflections that contradict the character's pose; (6) art-style breaks \
into 2D/anime; (7) hard world jumps at cuts. Report every finding as: sheet, row \
number, frame number, defect, severity (critical/minor). End with verdict \
SHIP or FIX."""


def visual_checklist_qa(proj: Path) -> Path | None:
    """LLM defect-checklist pass over the strip sheets (needs agy)."""
    review = proj / "review"
    bounds = sorted((review / "boundaries").glob("boundary_*.png"))
    dense = sorted((review / "dense").glob("dense_*.png"))
    if not bounds or not dense:
        return None
    _vstack(bounds, review / "boundaries_sheet.png")
    _vstack(dense, review / "dense_sheet.png")
    report = proj / "qa_visual_report.md"
    try:
        r = subprocess.run(
            ["agy", "--sandbox", "--dangerously-skip-permissions",
             "-p", CHECKLIST],
            capture_output=True, text=True, timeout=2400, cwd=review)
        if r.returncode != 0 or not r.stdout.strip():
            report.write_text(f"# Visual checklist DID NOT RUN\n\n"
                              f"exit {r.returncode}\n\n{r.stderr[-2000:]}\n")
            print(f"!! visual checklist QA failed (exit {r.returncode})")
        else:
            report.write_text(r.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        report.write_text(f"visual checklist QA unavailable: {e}\n")
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
        if v.get("verdict") not in ("SHIP", "FIX"):
            v = fallback
    except Exception as e:
        v = dict(fallback, summary=f"audit failed: {e}")
    (proj / "audit_findings.json").write_text(json.dumps(v, indent=1))
    return v


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
