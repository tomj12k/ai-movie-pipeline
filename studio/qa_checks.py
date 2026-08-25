"""Machine QA battery, run before the LLM review.

Checks encode what the human audits kept finding:
  - signal stats per stem (clipping, flatline, DC offset)
  - speech-recognition sweep (music/foley must carry no words;
    narration must transcribe)
  - boundary strips: 4 frames straddling every cut, for transition review
  - dense strips: 5 frames per clip, for character/style drift review
Results land in <project>/review/ and qa_machine_report.md.
"""
import re
import subprocess
from pathlib import Path

TTS_PY = Path.home() / ".studio-tts-venv" / "bin" / "python"


def signal_stats(wav: Path) -> dict:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(wav), "-af", "astats",
         "-f", "null", "-"], capture_output=True, text=True).stderr
    overall = out[out.rfind("Overall"):]
    grab = lambda k: float(m.group(1)) if (
        m := re.search(rf"{k}: (-?[\d.]+)", overall)) else None
    return {"peak_db": grab("Peak level dB"),
            "flat": grab("Flat factor"), "dc": grab("DC offset")}


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
    """5 evenly spaced frames per clip."""
    outdir.mkdir(parents=True, exist_ok=True)
    strips = []
    for clip in clips:
        frames = []
        for i in range(5):
            f = outdir / f"{clip.stem}_{i}.png"
            subprocess.run(["ffmpeg", "-y", "-v", "error",
                            "-ss", f"{2 + i * 4}", "-i", str(clip),
                            "-frames:v", "1", "-vf", "scale=320:-2", str(f)],
                           check=True)
            frames.append(f)
        strip = outdir / f"dense_{clip.stem}.png"
        _grid(frames, strip)
        for f in frames:
            f.unlink()
        strips.append(strip)
    return strips


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
        flags = []
        if s["peak_db"] is not None and s["peak_db"] > -0.5:
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
    if draft.exists():
        durs = [s.get("trim_frames", 48) / 24.0 for s in shots]
        n = len(boundary_strips(draft, durs, review / "boundaries"))
        lines.append(f"\n## Strips\n- {n} boundary strips → review/boundaries/")
    edit = Path.home() / "StudioProxies" / project / "edit"
    clips = sorted(edit.glob("[0-9]*.mp4")) if edit.exists() else []
    if clips:
        n = len(dense_strips(clips, review / "dense"))
        lines.append(f"- {n} dense strips → review/dense/")

    report = proj / "qa_machine_report.md"
    report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return report
