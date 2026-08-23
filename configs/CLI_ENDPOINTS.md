# CLI endpoint map

Every terminal AI tool in the studio, where it runs, and what it talks to.

| CLI | Runs on | Backend | Config location |
|-----|---------|---------|-----------------|
| `claude` (Claude Code) | Mac | Anthropic cloud — `claude-fable-5` / `claude-opus-5` | own login (`claude /login`); no file needed |
| `codex` | Mac | OpenAI cloud | `~/.codex/config.toml` (own login) |
| `agy` (Antigravity) | Mac | Google cloud | `~/.gemini/antigravity-cli/` (own OAuth). The old `gemini` CLI individual tier is retired — `studio qa` uses `agy` automatically |
| `opencode` | Mac | **local** Spark vLLM `http://spark-d1a9.local:8000/v1` | `~/.config/opencode/opencode.json` — copy of `opencode.mac.json` |
| `opencode` | Spark | **local** vLLM `http://127.0.0.1:8000/v1` | `~/.config/opencode/opencode.json` — copy of `opencode.spark.json` |

## Rules

- **Cloud directors** (`claude`, `codex`, `gemini`) authenticate through their
  own accounts. `studio.py` shells out to them by binary name — never wire
  their keys into this repo.
- **Local loop** is OpenAI-compatible vLLM on the Spark serving `Qwen3.8-27B`
  (FP8, 131k context, tool calling via the qwen3_coder parser). Any
  OpenAI-compatible client works with base URL `http://spark-d1a9.local:8000/v1`
  and any non-empty API key.
- The Spark's ComfyUI API is `http://spark-d1a9.local:8188` (no auth — LAN only).

## Verify

```bash
curl http://spark-d1a9.local:8000/v1/models          # -> Qwen3.8-27B
curl http://spark-d1a9.local:8188/system_stats        # -> ComfyUI 0.27.x
opencode run "hello"                                   # answers via the Spark
```
