# Video Localization Workflow

This document describes the proven, reproducible workflow for the video localization runtime.

The current reference implementation targets Chinese-to-English localization, but the architecture is not limited to a single language pair.

## Goal

- Preserve original background audio and ambience
- Remove Chinese vocals as much as possible
- Replace Chinese narration with English dubbing
- Overlay English subtitles
- Output a ready-to-review English video

## Pipeline

```
1. ASR        →  Extract audio, generate zh.srt
2. Translate  →  Chinese subtitles → natural spoken English
3. TTS        →  Generate English voiceover (voiceover.wav)
4. Subtitle   →  Convert en.srt → styled en.ass
5. Compose    →  Merge video + voiceover + subtitles + BGM → final .en.mp4
```

## Default Tech Stack

- **ASR**: Qwen3-ASR
- **Translate**: OpenAI-compatible API
- **TTS**: VibeVoice-Realtime-0.5B (default) / VoxCPM-0.5B (clone mode)
- **Subtitle**: pysubs2 + ASS format
- **Compose**: ffmpeg
- **Background preservation**: demucs + ffmpeg amix

## Recommended Compose Settings

```yaml
compose:
  audio_mode: dub_plus_bgm
  enable_source_separation: true
  source_separation_provider: demucs
  source_separation_model: htdemucs
  bgm_gain_db: -12
```

This configuration:
1. Extracts the original audio track
2. Uses `demucs --two-stems=vocals` to isolate `no_vocals.wav`
3. Replaces Chinese narration with English dubbing
4. Mixes background audio back at reduced volume

## Setup

### 1. Install Dependencies

```bash
uv sync
brew install ffmpeg
```

### 2. Install Source Separation (Optional)

```bash
uv --native-tls pip install demucs
uv --native-tls pip install torchcodec
```

### 3. Verify Environment

```bash
uv run python -m scripts.doctor
```

## Daily Usage

### Single Video

```bash
uv run python -m scripts.process_single input/your_video.mp4
```

With voice cloning:

```bash
uv run python -m scripts.process_single input/your_video.mp4 --voice-clone
```

With voice cloning + line-by-line alignment:

```bash
uv run python -m scripts.process_single input/your_video.mp4 --voice-clone --line-sync
```

### Batch Processing

```bash
uv run python -m scripts.process_batch
```

## Workspace Artifacts

Each video produces artifacts in `workspace/<video_name>/`:

| File | Description |
|------|-------------|
| `source_audio.wav` | Audio extracted from source video |
| `zh.srt` | Chinese transcription |
| `en.srt` | English translation |
| `en.ass` | Styled English subtitles |
| `voiceover.wav` | English voiceover |
| `status.json` | Step completion state |
| `manifest.json` | Input/output/config snapshot |

Clone mode adds: `clone_reference.wav`, `clone_reference.txt`, `clone_reference.vocals.wav`

Debug mode adds: `debug/` directory with reports and segment previews

Final output: `output/<video_name>.en.mp4`

## Resume Behavior

Default: `resume: true`. The pipeline checks both step completion status and config snapshots. Changing any config section automatically re-runs the affected step and all downstream steps.

## English Segmentation Strategy

### Translation Stage

Treats consecutive subtitles as continuous narration. Preserves original time slot count but allows content redistribution across adjacent slots to avoid splitting names, dates, or phrases awkwardly.

### TTS Stage

Merges adjacent short segments using sentence-aware heuristics:
- Broken word endings
- Dangling prepositions/conjunctions
- Continuation signals in the next segment
- Duration and character length limits

After merging, optionally smooths the combined text for natural speech flow.

`--line-sync` disables merging for strict per-subtitle alignment (more TTS calls, slower, but tighter timing).

## Voice Cloning Strategy

Two distinct paths:
- **Default**: `tts.provider: vibevoice_realtime` (description-based voice)
- **Clone**: `--voice-clone` flag, switches to `tts.provider: voxcpm2`

Clone mode automatically:
1. Extracts a clean voice segment from source audio
2. Writes `clone_reference.wav` and `clone_reference.txt`
3. Synthesizes English dubbing using the reference voice

## Debugging Alignment

For timing issues, debug individual segments instead of re-processing the entire video:

```bash
# View a TTS chunk's boundaries
uv run python -m scripts.debug_alignment input/your_video.mp4 --tts-chunk 1 --report-only

# Compare strategies
uv run python -m scripts.debug_alignment input/your_video.mp4 --tts-chunk 1 --report-only --line-sync

# Re-synthesize one segment
uv run python -m scripts.debug_alignment input/your_video.mp4 --tts-chunk 1 --resynthesize --line-sync
```

## Troubleshooting

| Symptom | Check |
|---------|-------|
| No background audio | Run `scripts.doctor`, verify `demucs` + `torchcodec` installed. Check `manifest.json` for `effective_audio_mode` |
| Subtitle size unchanged | Verify `config.yaml` `subtitle.font_size`, check `manifest.json` config snapshot |
| Unnatural English | Check `en.srt` quality first, then `tts.metadata.merged_segment_count` in manifest |

### Recommended Debug Order

1. `uv run python -m scripts.doctor`
2. `workspace/<video_name>/manifest.json`
3. `workspace/<video_name>/zh.srt`
4. `workspace/<video_name>/en.srt`
5. `workspace/<video_name>/voiceover.wav`
6. `output/<video_name>.en.mp4`

---

[中文文档](WORKFLOW.zh-CN.md)
