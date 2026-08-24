#!/usr/bin/env bash
# One-time: local Kokoro TTS (narrator voice) on the Mac.
# Creates ~/.studio-tts-venv and fetches the onnx model + voice bank (~340MB).
set -euo pipefail

VENV="$HOME/.studio-tts-venv"
MODELS="$VENV/models"

[[ -d "$VENV" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" -q install --upgrade kokoro-onnx soundfile numpy

mkdir -p "$MODELS"
BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
[[ -f "$MODELS/kokoro-v1.0.onnx" ]] || curl -L -o "$MODELS/kokoro-v1.0.onnx" "$BASE/kokoro-v1.0.onnx"
[[ -f "$MODELS/voices-v1.0.bin" ]]  || curl -L -o "$MODELS/voices-v1.0.bin"  "$BASE/voices-v1.0.bin"

"$VENV/bin/python" - <<'EOF'
from kokoro_onnx import Kokoro
from pathlib import Path
m = Path.home()/".studio-tts-venv/models"
k = Kokoro(str(m/"kokoro-v1.0.onnx"), str(m/"voices-v1.0.bin"))
a, sr = k.create("The studio narrator is ready.", voice="am_michael")
print(f"TTS OK: {len(a)/sr:.1f}s sample @ {sr}Hz")
EOF
echo "done — run: ~/.studio-tts-venv/bin/python studio/narrate.py --project <name>"
