#!/usr/bin/env python3
"""Generate the narrator stem for a project with local Kokoro TTS.

Reads <project>/narration.json: [{"time": seconds, "text": "..."}, ...]
Writes <project>/audio/narration_track.wav (full-length timed track, 48kHz)
plus each line as audio/vo_NN.wav for manual placement in Resolve.

Setup (one-time, done by setup_tts.sh):
  python3 -m venv ~/.studio-tts-venv
  ~/.studio-tts-venv/bin/pip install kokoro-onnx soundfile numpy
  model + voices downloaded next to this script's cache dir.

Usage: ~/.studio-tts-venv/bin/python studio/narrate.py --project niko_and_pip \
         [--voice am_michael] [--speed 0.95] [--length 300]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

CACHE = Path.home() / ".studio-tts-venv" / "models"
SR = 24000  # kokoro native


def safe_project(name: str) -> str:
    """Reject anything but a plain folder name: an absolute path would replace
    the share root in the join below, and '..' would walk out of it."""
    if not name or name != Path(name).name or name in (".", "..") \
            or name.startswith("."):
        raise SystemExit(f"✗ invalid --project {name!r}: use a plain folder name")
    return name


def projects_root() -> Path:
    for p in (Path("/Volumes/Active_Projects"),
              Path.home() / "StudioMounts/Active_Projects",
              Path("/mnt/synology/projects")):
        if p.is_dir():
            return p
    raise SystemExit("Active_Projects share is not mounted")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--voice", default="am_michael")
    ap.add_argument("--speed", type=float, default=0.95)
    ap.add_argument("--length", type=float, default=300.0)
    a = ap.parse_args()

    proj = projects_root() / safe_project(a.project)
    cues = json.loads((proj / "narration.json").read_text())
    outdir = proj / "audio"
    outdir.mkdir(exist_ok=True)

    kokoro = Kokoro(str(CACHE / "kokoro-v1.0.onnx"), str(CACHE / "voices-v1.0.bin"))
    track = np.zeros(int(a.length * SR), dtype=np.float32)

    for i, cue in enumerate(cues, 1):
        audio, sr = kokoro.create(cue["text"], voice=a.voice, speed=a.speed)
        audio = audio.astype(np.float32)
        sf.write(outdir / f"vo_{i:02d}.wav", audio, sr)
        start = int(cue["time"] * SR)
        end = min(start + len(audio), len(track))
        track[start:end] += audio[: end - start]
        print(f"vo_{i:02d}: {cue['time']:>5.1f}s  {len(audio)/sr:5.1f}s  "
              f"\"{cue['text'][:48]}…\"")
        if end == len(track) and start + len(audio) > len(track):
            print(f"  !! vo_{i:02d} runs past --length and was clipped")

    peak = float(np.abs(track).max()) or 1.0
    track *= min(1.0, 0.89 / peak)
    sf.write(outdir / "narration_track.wav", track, SR)
    print(f"\nnarration_track.wav: {a.length:.0f}s @ {SR}Hz, peak {peak:.2f} -> 0.89")


if __name__ == "__main__":
    main()
