---
name: video-localization-pipeline
description: Use when Codex needs to operate a compatible repo that generates localized video versions across languages via `scripts.process_single`, `scripts.process_batch`, `scripts.debug_alignment`, `workspace/` artifacts, automatic voice cloning with `--voice-clone`, multilingual dubbing or subtitling outputs, or tighter subtitle-to-speech timing with `--line-sync`.
---

# Video Localization Pipeline

Use this skill for a compatible video localization runtime repo, or a close fork of it. Prefer the repo's own scripts, config snapshots, and workspace artifacts over ad hoc ffmpeg commands or one-off shell pipelines.

This skill does not include the runtime, providers, or model assets needed to render videos by itself. If the user only has the skill repository installed, explain that a companion runtime repo is required.

## Companion Runtime Requirement

- Expect a checked-out runtime repo with the actual pipeline scripts
- Do not treat the skill distribution repo as the execution environment
- If the expected runtime files are missing, stop and explain that the user needs a compatible runtime repo before video generation can work

## Repo Check

Work from the repo root and confirm these files exist before running pipeline commands:

- `config.yaml`
- `scripts/process_single.py`
- `scripts/process_batch.py`
- `scripts/debug_alignment.py`
- `src/pipeline.py`

If they do not exist in the current directory, find the correct repo root first. If the current checkout is only the skill repo, do not improvise a full rendering stack from scratch; explain that the execution layer is missing.

## Quick Start

- From a compatible runtime repo, run one video with the default flow:

```bash
uv run python -m scripts.process_single input/sample.mp4
```

- Run one video with automatic voice cloning:

```bash
uv run python -m scripts.process_single input/sample.mp4 --voice-clone
```

- Run one video with tighter line-by-line timing:

```bash
uv run python -m scripts.process_single input/sample.mp4 --voice-clone --line-sync
```

- Run environment checks when providers or dependencies look broken:

```bash
uv run python -m scripts.doctor
```

## Core Workflow

1. Confirm you are in the correct runtime repo root, not just the skill distribution repo.
2. Run `scripts.doctor` if the task smells like setup, provider, or dependency trouble.
3. Choose the lightest pipeline mode that matches the request:
   - default mode for the repo's normal localized output flow
   - `--voice-clone` when the user wants the target-language voice to follow the source speaker
   - `--line-sync` when sentence boundary timing matters more than merged TTS phrasing
4. Inspect `workspace/<video>/manifest.json` before guessing what actually ran.
5. Use `scripts.debug_alignment` when the complaint is local to a few sentences.

## Reference Map

Read only what you need:

- `references/commands.md` for canonical run commands and expected artifacts
- `references/troubleshooting.md` for timing diagnosis, manifest fields, and debug workflows

If the checked-out repo also contains project docs such as `README.md` or `WORKFLOW.md`, read them only when you need repo-specific defaults or current operational notes.

## Guardrails

- If the required runtime files are missing, clearly say that the skill alone cannot generate videos.
- Treat `manifest.json` as the source of truth for provider choice, merge behavior, and config snapshots.
- Reuse the repo's resume logic; do not delete `workspace/` unless rerun detection is clearly insufficient.
- Expect `--line-sync` to be slower because it removes or sharply reduces TTS merging.
- Expect clone mode to use `voxcpm2`; do not assume `vibevoice_realtime` can clone arbitrary reference audio.
- When the issue is only a short timing window, prefer `scripts.debug_alignment` over rerendering the full video.
