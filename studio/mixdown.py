"""Final master mixdown: end card + three-stem mix over the draft reel.

Levels live in an optional <project>/mix.json so they can be tuned per
project without code changes:
  {"foley": 0.75, "music": 0.30, "narration": 1.25,
   "card_seconds": 8.0, "title": "Niko & Pip", "subtitle": "The End"}
The music bed is sidechain-ducked under the narration so the storyteller
always sits on top of the score.
"""
import json
import shutil
import subprocess
from pathlib import Path

DEFAULTS = {"foley": 0.75, "music": 0.30, "narration": 1.25,
            "card_seconds": 8.0, "title": None, "subtitle": "The End"}
TTS_PY = Path.home() / ".studio-tts-venv" / "bin" / "python"


def _probe_dur(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out)


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
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.1",
                    "-i", str(draft), "-frames:v", "1", str(last)], check=True)
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

    inputs = ["-i", str(draft), "-i", str(card)]
    fc = ["[0:v][1:v]concat=n=2:v=1:a=0[v]",
          f"[0:a]volume={cfg['foley']},apad[fol]"]
    mix_in, n = ["[fol]"], 2
    if narr.exists():
        inputs += ["-i", str(narr)]
        fc.append(f"[{n}:a]volume={cfg['narration']},apad,asplit=2[n1][n2]")
        n += 1
    if music:
        inputs += ["-i", str(music)]
        duck = ("[n2]sidechaincompress=threshold=0.02:ratio=6:attack=100"
                ":release=800[mus]" if narr.exists() else "anull[mus]")
        fc.append(f"[{n}:a]volume={cfg['music']},apad[m0]")
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

    local = Path.home() / "StudioProxies" / project
    local.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(final, local / final.name)
    # Only archive to a real mount: mkdir on an absent mount would quietly
    # create a local folder and the "archive" would never reach the NAS.
    archive_root = Path.home() / "StudioMounts/Portfolio_Archive"
    if archive_root.is_mount() or archive_root.is_dir() and \
            any(archive_root.iterdir()):
        dest = archive_root / project
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(final, dest / final.name)
    else:
        print(f"!! Portfolio_Archive is not mounted — master NOT archived "
              f"(local copy only: {local / final.name})")
    return final
