# Video Localization Pipeline Commands

Run commands from the repo root.

## Repo check

Confirm the expected scripts exist:

```bash
test -f config.yaml
test -f scripts/process_single.py
test -f scripts/process_batch.py
test -f scripts/debug_alignment.py
test -f src/pipeline.py
```

## Health check

Use this before changing providers or when dependencies look missing:

```bash
uv run python -m scripts.doctor
```

## Main runs

Default single-video run:

```bash
uv run python -m scripts.process_single input/sample.mp4
```

Single-video run with automatic voice cloning:

```bash
uv run python -m scripts.process_single input/sample.mp4 --voice-clone
```

Single-video run with voice cloning plus tighter subtitle-to-speech timing:

```bash
uv run python -m scripts.process_single input/sample.mp4 --voice-clone --line-sync
```

Single-video run with a manual reference clip:

```bash
uv run python -m scripts.process_single input/sample.mp4 \
  --voice-clone \
  --reference-wav /absolute/path/to/ref.wav \
  --reference-text "reference transcript"
```

Batch run:

```bash
uv run python -m scripts.process_batch
```

## Alignment debug

Inspect merged chunk boundaries without rendering a preview:

```bash
uv run python -m scripts.debug_alignment input/sample.mp4 --tts-chunk 1 --report-only
```

Compare current timing with `--line-sync` in the same window:

```bash
uv run python -m scripts.debug_alignment input/sample.mp4 --tts-chunk 1 --report-only --line-sync
```

Re-synthesize only the short window:

```bash
uv run python -m scripts.debug_alignment input/sample.mp4 --tts-chunk 1 --resynthesize --line-sync
```

## Expected artifacts

For `input/foo.mov`, expect:

- `workspace/foo/source_audio.wav`
- `workspace/foo/zh.srt`
- `workspace/foo/en.srt`
- `workspace/foo/en.ass`
- `workspace/foo/voiceover.wav`
- `workspace/foo/status.json`
- `workspace/foo/manifest.json`
- `output/foo.en.mp4`

Clone mode commonly adds:

- `workspace/foo/clone_reference.wav`
- `workspace/foo/clone_reference.txt`
- `workspace/foo/clone_reference.vocals.wav`
