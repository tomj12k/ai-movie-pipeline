# Character LoRA — niko_pip_v2

Trains a LoKr character adapter for Niko and Pip on Krea-2-Raw, to fix the
film's core defect: within a single clip the character's geometry morphs
continuously (ear shape changes frame to frame while the background stays
static). Prompt wording cannot constrain that — the model has no persistent
representation of the character and re-invents it each frame.

## Dataset
`~/ai/datasets/niko_pip_v2` on the Spark — 46 images taken from the Krea2
KEYFRAME stills (`p*_take_frame_*.png`), not from video frames: the keyframes
are the image stage, before any temporal morphing, so they are the cleanest
on-model source available.

Captions use the trigger `nkp` and describe only what VARIES (scene, pose), so
the trigger absorbs the character design itself.

## Running it
Training needs the Spark's memory. vLLM holds ~52 GB, which leaves too little
and has crashed the box before, so stop it first (it runs as `pizzacat` with
`Restart=on-failure`, so a clean SIGTERM stops it without sudo):

    pkill -TERM -f '[v]llm'
    ssh spark 'cd ~/ai/ai-toolkit && ~/ai/venv_train/bin/python run.py \
        ~/ai/training-configs/niko_pip_v2.yaml'

`~/ai/venv_train` is a CLONE of the ComfyUI venv. Install training deps there,
never into `~/ai/comfyui/venv032` — the requirements downgrade torch, and
ComfyUI must keep 2.13.0.

Restart vLLM afterwards: `sudo systemctl start vllm-qwen38`.

## Parameters
768 buckets (not the native 1280x704 — 3x faster and ample for character
identity), 1200 steps at ~12 s/step, checkpoints every 200 steps.
