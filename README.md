# Video Localization

Multilingual video localization runtime with voice cloning, line-sync timing control, and a built-in agent skill.

```
Input Video (zh) ──► ASR ──► Translate ──► TTS ──► Subtitle ──► Compose ──► Output Video (en)
                   Qwen3    OpenAI API   VoxCPM    pysubs2      ffmpeg
                            compatible   /VibeVoice
```

## Motivation

This project started after [OpenBMB](https://github.com/OpenBMB) released [VoxCPM](https://github.com/OpenBMB/VoxCPM), a voice cloning model that can reproduce a speaker's voice in a different language. I wanted to see how far I could push it in a real-world scenario: taking Chinese short videos and producing fully dubbed English versions — with the original speaker's voice, background audio preserved, and subtitles aligned.

What began as a quick experiment turned into a complete video localization pipeline.

## Tech Stack

| Stage | Default Provider | Role |
|-------|-----------------|------|
| ASR | [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-0.6B) | Speech-to-text with forced alignment |
| Translate | OpenAI-compatible API | Context-aware subtitle translation |
| TTS | [VibeVoice-Realtime](https://github.com/microsoft/VibeVoice) | English voice synthesis |
| TTS (clone) | [VoxCPM](https://github.com/OpenBMB/VoxCPM) | Voice cloning from source speaker |
| Separation | [Demucs](https://github.com/adefossez/demucs) | Vocal removal to preserve BGM |
| Compose | FFmpeg | Final video assembly with subtitles |

## Quick Start

```bash
# Install
uv sync
brew install ffmpeg

# Check environment
uv run python -m scripts.doctor

# Process a video
uv run python -m scripts.process_single input/sample.mp4
```

## Voice Cloning

The headline feature. Powered by VoxCPM, this automatically extracts a reference voice clip from the source video and synthesizes the English dub in the original speaker's voice:

```bash
uv run python -m scripts.process_single input/sample.mp4 --voice-clone
```

For tighter subtitle-to-speech alignment:

```bash
uv run python -m scripts.process_single input/sample.mp4 --voice-clone --line-sync
```

You can also provide your own reference audio:

```bash
uv run python -m scripts.process_single input/sample.mp4 \
  --voice-clone \
  --reference-wav /path/to/ref.wav \
  --reference-text "reference transcript"
```

## Pipeline Overview

Each video produces a workspace directory with intermediate artifacts:

```
workspace/<video_name>/
├── source_audio.wav      # Extracted audio
├── zh.srt                # Chinese transcription
├── en.srt                # English translation
├── en.ass                # Styled English subtitles
├── voiceover.wav         # English voiceover
├── status.json           # Step completion state
└── manifest.json         # Full execution record
```

Final output: `output/<video_name>.en.mp4`

### Resumable Runs

The pipeline tracks step completion and config snapshots. If you change a config value (e.g., TTS provider), only the affected step and its downstream steps re-run automatically.

### Batch Processing

```bash
uv run python -m scripts.process_batch
```

## Configuration

All settings live in `config.yaml`. Key sections:

- **asr**: Provider selection, model paths, language
- **translate**: API endpoint, model, batch size, word-rate limits
- **tts**: Provider, voice mode, segment merging strategy, tempo limits
- **subtitle**: Font, size, position, colors
- **compose**: Audio mode (`dub_only` or `dub_plus_bgm`), source separation

### Alternative Providers

Use SiliconFlow for translation:

```bash
export SILICONFLOW_API_KEY=...
uv run python -m scripts.process_single input/sample.mp4 --config config.siliconflow.yaml
```

### Environment Variables

Copy `.env.example` to `.env` and fill in your keys:

- `OPENAI_API_KEY` — Default translation provider
- `SILICONFLOW_API_KEY` — Alternative translation provider
- `ANTHROPIC_API_KEY` — For Claude API translation

## Debugging Alignment

When timing feels off, debug a specific segment without re-processing the entire video:

```bash
# Inspect a TTS chunk
uv run python -m scripts.debug_alignment input/sample.mp4 --tts-chunk 1 --report-only

# Compare default vs line-sync strategy
uv run python -m scripts.debug_alignment input/sample.mp4 --tts-chunk 1 --report-only --line-sync

# Re-synthesize just that segment
uv run python -m scripts.debug_alignment input/sample.mp4 --tts-chunk 1 --resynthesize --line-sync
```

## Agent Skill

This repo includes a built-in agent skill under `skills/video-localization-pipeline/` for integration with Codex, Claude Code, OpenClaw, and opencode. See [AGENT_SKILL.md](AGENT_SKILL.md) for installation instructions.

The skill helps agents operate this runtime — it does not replace it.

## License

[MIT](LICENSE)

---

[中文文档](README.zh-CN.md)
