# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

Video localization runtime that converts videos from one language to another (currently Chinese to English). The pipeline: **ASR -> Translate -> TTS -> Subtitle -> Compose**. Each step produces intermediate files in `workspace/<video_name>/` and the final output goes to `output/<video_name>.en.mp4`.

## Common Commands

```bash
# Install dependencies
uv sync
brew install ffmpeg

# Check environment readiness
uv run python -m scripts.doctor

# Process a single video
uv run python -m scripts.process_single input/sample.mp4

# With voice cloning
uv run python -m scripts.process_single input/sample.mp4 --voice-clone

# With voice cloning + line-by-line alignment
uv run python -m scripts.process_single input/sample.mp4 --voice-clone --line-sync

# Batch process
uv run python -m scripts.process_batch

# Debug time alignment for a specific TTS chunk
uv run python -m scripts.debug_alignment input/sample.mp4 --tts-chunk 1 --report-only

# Use SiliconFlow config
uv run python -m scripts.process_single input/sample.mp4 --config config.siliconflow.yaml

# Lint
uv run ruff check src/ scripts/
```

## Architecture

### Pipeline (`src/pipeline.py`)

The core orchestrator. Runs 5 sequential steps, each producing artifacts in `workspace/<video_name>/`:

| Step | Module | Input | Output |
|------|--------|-------|--------|
| asr | `src/asr.py` | source video | `source_audio.wav`, `zh.srt` |
| translate | `src/translate.py` | `zh.srt` | `en.srt` |
| tts | `src/tts.py` | `en.srt` | `voiceover.wav` |
| subtitle | `src/subtitle.py` | `en.srt` | `en.ass` |
| compose | `src/compose.py` | video + voiceover + subtitle | final `.en.mp4` |

Each step returns a dict with `provider`, `inputs`, `outputs`, `metadata` keys. The pipeline writes `status.json` (step states) and `manifest.json` (full execution record with config snapshots) for resume/replay.

### Resume Logic

`resume: true` by default. Skipping a step requires both: the step was previously "completed" AND its config snapshot hasn't changed. If config changes, that step and all downstream steps re-run automatically.

### Configuration (`src/models.py`)

All config is plain dataclasses: `AppConfig` -> `AsrConfig`, `TranslateConfig`, `TtsConfig`, `SubtitleStyleConfig`, `ComposeConfig`. Loaded from `config.yaml` (or `--config` override), merged with CLI flags.

### Provider Pattern

ASR, translate, and TTS modules each support multiple providers selected via config:

- **ASR**: `qwen3_asr` (default, local model), `faster_whisper` (fallback)
- **Translate**: `openai_compatible` (default, any OpenAI-compatible API), `claude_api`, `claude_code`
- **TTS**: `vibevoice_realtime` (default, description-based voice), `voxcpm2` (voice cloning), `macos_say` (fallback)
- **Source separation**: `demucs` (for removing vocals and preserving BGM)

### TTS Segment Merging

TTS doesn't process subtitles 1:1. It merges adjacent short segments using sentence-aware heuristics (dangling words, continuation signals, duration/length limits) then optionally smooths the merged text. `--line-sync` disables merging for strict per-subtitle alignment.

### Voice Cloning Flow

`--voice-clone` triggers: extract reference audio from source -> `clone_reference.wav` + `clone_reference.txt` -> switch TTS provider to `voxcpm2`. The `src/voice_clone.py` module handles reference extraction.

### Scripts

- `scripts/process_single.py` - Main CLI entry point, parses args, loads config, calls `pipeline.process_video()`
- `scripts/process_batch.py` - Processes all videos in `input/`
- `scripts/doctor.py` - Environment checker (ffmpeg, models, API keys, Python packages)
- `scripts/debug_alignment.py` - Inspect/resynthesize individual TTS chunks for debugging timing
- `scripts/spike_*.py` - Standalone smoke tests for individual components

### Agent Skill

`skills/video-localization-pipeline/` contains an agent integration layer (SKILL.md + OpenAI agent config). The skill helps agents call the runtime scripts; it doesn't replace the runtime.

## Environment Variables

See `.env.example`. Key ones:
- `OPENAI_API_KEY` - Default translation provider
- `SILICONFLOW_API_KEY` - Alternative translation provider
- `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` - For `claude_api` translate provider

## Documentation

Docs are bilingual: English primary (`README.md`, `WORKFLOW.md`, `AGENT_SKILL.md`) with Chinese versions (`*.zh-CN.md`).

## Code Style

- Python 3.11+, managed with `uv`
- Linter: `ruff` (line-length 100)
- CI: GitHub Actions runs `ruff check` on push/PR
- No test suite currently exists
- All modules use `from __future__ import annotations`
