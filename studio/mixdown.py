"""Final master mixdown: end card + three-stem mix over the draft reel.

Levels live in an optional <project>/mix.json so they can be tuned per
project without code changes:
  {"foley": 0.75, "music": 0.30, "narration": 1.25,
   "card_seconds": 8.0, "title": "Niko & Pip", "subtitle": "The End"}
The music bed is sidechain-ducked under the narration so the storyteller
always sits on top of the score.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

DEFAULTS = {"foley": 0.75, "music": 0.30, "narration": 1.25,
            "card_seconds": 8.0, "title": None, "subtitle": "The End",
            # Lowering stem levels to rebalance also lowers the whole film, so
            # the master is re-targeted afterwards. -16 LUFS suits streaming.
            "target_lufs": -16.0}
TTS_PY = Path.home() / ".studio-tts-venv" / "bin" / "python"


def _probe_dur(path: Path) -> float:
    """Raises with a clear message rather than ValueError/hanging: the draft
    lives on an SMB share, so this is a likely place to stall."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True,
                             timeout=60).stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffprobe timed out on {path} — NAS stalled?")
    try:
        return float(out)
    except ValueError:
        raise RuntimeError(f"cannot read duration of {path} (got {out!r}); "
                           f"is the draft reel complete?")


def _title_overlay(work: Path, title: str, subtitle: str) -> Path | None:
    """Render the title card text to a transparent PNG (Pillow via the TTS
    venv, which is the one environment guaranteed to have it)."""
    png = work / "title_overlay.png"
    script = (
        "from PIL import Image, ImageDraw, ImageFont\n"
        "im = Image.new('RGBA', (1280, 704), (0, 0, 0, 0))\n"
        "d = ImageDraw.Draw(im)\n"
        "f1 = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 110)\n"
        "f2 = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 54)\n"
        f"t1, t2 = {title!r}, {subtitle!r}\n"
        "for t, f, y in ((t1, f1, 250), (t2, f2, 400)):\n"
        "    w = d.textlength(t, font=f)\n"
        "    d.text(((1280 - w) / 2 + 3, y + 3), t, font=f, fill=(0, 0, 0, 160))\n"
        "    d.text(((1280 - w) / 2, y), t, font=f, fill=(255, 250, 235, 255))\n"
        f"im.save({str(png)!r})\n")
    py = TTS_PY if TTS_PY.exists() else Path("python3")
    r = subprocess.run([str(py), "-c", script], capture_output=True, text=True)
    return png if r.returncode == 0 and png.exists() else None


def build_end_card(draft: Path, work: Path, cfg: dict) -> Path:
    """8s card: slow zoom on the film's last frame, title fading in/out."""
    dur = cfg["card_seconds"]
    last = work / "card_last_frame.png"
    # Absolute seek, not -sseof: after an xfade chain the end-relative seek
    # can land past the final frame and silently encode nothing.
    draft_dur = _probe_dur(draft)
    r = None
    for back in (0.3, 0.6, 1.0):
        last.unlink(missing_ok=True)
        r = subprocess.run(["ffmpeg", "-y", "-v", "error",
                            "-ss", f"{max(0.0, draft_dur - back):.3f}",
                            "-i", str(draft), "-frames:v", "1", str(last)],
                           capture_output=True, text=True)
        if r.returncode == 0 and last.is_file():
            break
    if r is None or r.returncode != 0 or not last.is_file():
        raise RuntimeError(f"cannot read the last frame of {draft.name} — "
                           f"the draft reel is missing or truncated "
                           f"({r.stderr.strip()[-200:]})")
    card = work / "end_card.mp4"
    overlay = _title_overlay(work, cfg["title"], cfg["subtitle"]) \
        if cfg["title"] else None
    zoom = (f"zoompan=z='1+0.10*on/({dur}*24)':d={int(dur * 24)}"
            f":s=1280x704:fps=24")
    if overlay:
        fc = (f"[0:v]{zoom}[z];[1:v]format=rgba,"
              f"fade=t=in:st=1.0:d=1.8:alpha=1,"
              f"fade=t=out:st={dur - 1.0}:d=1.0:alpha=1[t];"
              f"[z][t]overlay[v]")
        cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(last),
               "-loop", "1", "-i", str(overlay), "-filter_complex", fc,
               "-map", "[v]", "-t", f"{dur}", "-r", "24",
               "-c:v", "libx264", "-preset", "fast", "-crf", "18",
               "-pix_fmt", "yuv420p", str(card)]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(last),
               "-vf", zoom + f",fade=t=out:st={dur - 1.0}:d=1.0",
               "-t", f"{dur}", "-r", "24", "-c:v", "libx264",
               "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
               str(card)]
    subprocess.run(cmd, check=True)
    return card


def measure_lufs(path: Path):
    """Integrated loudness, or None if it can't be measured."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path),
                              "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=900).stderr
    except subprocess.TimeoutExpired:
        return None
    m = re.search(r"I:\s+(-?\d+\.\d+)\s+LUFS", out[out.rfind("Summary"):])
    return float(m.group(1)) if m else None


def _retarget_loudness(final: Path, work: Path, target):
    """Apply a measured gain so the rebalanced mix keeps broadcast level.
    Plain gain + limiter, not loudnorm: loudnorm truncates the stream EOF in
    this ffmpeg build and would clip the tail off the master."""
    if not target:
        return final
    cur = measure_lufs(final)
    if cur is None:
        print("!! could not measure loudness — master left at mix level")
        return final
    gain = target - cur
    if abs(gain) < 0.5:
        print(f"  loudness {cur:.1f} LUFS already on target")
        return final
    tmp = work / f"leveled_{final.name}"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(final),
                    "-af", f"volume={gain:.2f}dB,alimiter=limit=0.95",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(tmp)],
                   check=True)
    shutil.move(str(tmp), str(final))
    after = measure_lufs(final)
    print(f"  loudness {cur:.1f} -> {after:.1f} LUFS (target {target}, "
          f"{gain:+.1f} dB)")
    return final


def final_mix(proj: Path, project: str) -> Path:
    """Draft reel + end card, foley/music/narration mixed at cfg levels."""
    cfg = dict(DEFAULTS)
    mix_json = proj / "mix.json"
    if mix_json.exists():
        cfg.update(json.loads(mix_json.read_text()))
    if cfg["title"] is None:
        cfg["title"] = project.replace("_", " ").title()
    draft = proj / "draft_reel.mp4"
    audio = proj / "audio"
    music = next(iter(sorted(audio.glob("music*.wav"))), None)
    narr = audio / "narration_track.wav"
    work = Path.home() / "StudioProxies" / project / "assemble_work"
    work.mkdir(parents=True, exist_ok=True)

    # A missing stem silently produces a foley-only "master" that still gets
    # archived — say so loudly instead.
    absent = [n for n, p in (("music", music), ("narration", narr))
              if not p or not p.exists()]
    if absent:
        print(f"!! MIXING WITHOUT {', '.join(absent).upper()} — "
              f"the master will not contain {' or '.join(absent)}")

    card = build_end_card(draft, work, cfg)
    total = _probe_dur(draft) + cfg["card_seconds"]
    fade_at = total - 1.7

    # Stems differ in rate/layout (Kokoro narration is 24k mono, score is 48k
    # stereo) and sidechaincompress needs both of its inputs to match, so every
    # stem is conformed explicitly rather than relying on auto-negotiation.
    FMT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    inputs = ["-i", str(draft), "-i", str(card)]
    fc = ["[0:v][1:v]concat=n=2:v=1:a=0[v]",
          f"[0:a]volume={cfg['foley']},{FMT},apad[fol]"]
    mix_in, n = ["[fol]"], 2
    if narr.exists():
        inputs += ["-i", str(narr)]
        fc.append(f"[{n}:a]volume={cfg['narration']},{FMT},apad,asplit=2[n1][n2]")
        n += 1
    if music:
        inputs += ["-i", str(music)]
        duck = ("[n2]sidechaincompress=threshold=0.02:ratio=6:attack=100"
                ":release=800[mus]" if narr.exists() else "anull[mus]")
        fc.append(f"[{n}:a]volume={cfg['music']},{FMT},apad[m0]")
        fc.append(f"[m0]{duck}")
        mix_in.append("[mus]")
        n += 1
    if narr.exists():
        mix_in.append("[n1]")
    fc.append("".join(mix_in) +
              f"amix=inputs={len(mix_in)}:duration=longest:normalize=0,"
              f"alimiter=limit=0.92,atrim=0:{total:.3f},"
              f"afade=t=out:st={fade_at:.3f}:d=1.7[a]")
    final = proj / f"{project}_final.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error"] + inputs +
                   ["-filter_complex", ";".join(fc),
                    "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k",
                    "-t", f"{total:.3f}", str(final)], check=True)

    final = _retarget_loudness(final, work, cfg.get("target_lufs"))

    local = Path.home() / "StudioProxies" / project
    local.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(final, local / final.name)
    # Only archive to a real mount: mkdir on an absent mount would quietly
    # create a local folder and the "archive" would never reach the NAS.
    # is_mount() alone: a stale local dir left by an older buggy run would
    # otherwise satisfy a directory check and swallow the master again.
    archive_root = Path.home() / "StudioMounts/Portfolio_Archive"
    if archive_root.is_mount():
        dest = archive_root / project
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(final, dest / final.name)
    else:
        print(f"!! Portfolio_Archive is not mounted — master NOT archived "
              f"(local copy only: {local / final.name})")
    return final
