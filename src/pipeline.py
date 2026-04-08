from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter

from .asr import transcribe
from .compose import compose_video
from .models import AppConfig, StepRecord
from .subtitle import srt_to_styled_ass
from .translate import translate_srt
from .tts import generate_voiceover
from .utils import (
    init_manifest,
    load_status,
    project_root,
    resolve_path,
    save_manifest,
    save_status,
    setup_logging,
    step_config_snapshot,
    should_skip_step,
    utcnow_iso,
    workspace_for_video,
)

LOGGER = logging.getLogger("shorts.pipeline")


def process_video(video_path: Path, config: AppConfig) -> Path:
    setup_logging(config.runtime.log_level)

    root = project_root()
    resolved_video_path = video_path if video_path.is_absolute() else resolve_path(video_path, root)
    if not resolved_video_path.exists():
        raise FileNotFoundError(f"Input video not found: {resolved_video_path}")

    workspace_dir = workspace_for_video(resolved_video_path, config, root)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    output_dir = resolve_path(config.paths.output_dir, root)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_audio = workspace_dir / "source_audio.wav"
    zh_srt = workspace_dir / "zh.srt"
    en_srt = workspace_dir / "en.srt"
    en_ass = workspace_dir / "en.ass"
    voiceover_wav = workspace_dir / "voiceover.wav"
    final_video = output_dir / f"{resolved_video_path.stem}.en.mp4"

    status_path = workspace_dir / "status.json"
    manifest_path = workspace_dir / "manifest.json"
    status = load_status(status_path)
    manifest = init_manifest(manifest_path, resolved_video_path, config)

    steps = [
        (
            "asr",
            lambda: transcribe(resolved_video_path, source_audio, zh_srt, config),
            [source_audio, zh_srt],
        ),
        (
            "translate",
            lambda: translate_srt(zh_srt, en_srt, config),
            [en_srt],
        ),
        (
            "tts",
            lambda: generate_voiceover(en_srt, voiceover_wav, config),
            [voiceover_wav],
        ),
        (
            "subtitle",
            lambda: srt_to_styled_ass(en_srt, en_ass, config.subtitle),
            [en_ass],
        ),
        (
            "compose",
            lambda: compose_video(resolved_video_path, voiceover_wav, en_ass, final_video, config),
            [final_video],
        ),
    ]

    upstream_changed = False
    for step_name, action, outputs in steps:
        if should_skip_step(step_name, status, config, manifest, outputs, upstream_changed):
            LOGGER.info("Skipping completed step %s for %s", step_name, resolved_video_path.name)
            continue
        _run_step(step_name, action, status_path, manifest_path, status, manifest, config)
        upstream_changed = True

    manifest.outputs["final_video"] = str(final_video)
    manifest.metadata["config"] = config.to_dict()
    save_manifest(manifest_path, manifest)
    return final_video


def _run_step(
    step_name: str,
    action: callable,
    status_path: Path,
    manifest_path: Path,
    status: object,
    manifest: object,
    config: AppConfig,
) -> None:
    LOGGER.info("Running step %s", step_name)
    started_at = utcnow_iso()
    started_clock = perf_counter()
    status.set(step_name, "running")
    save_status(status_path, status)

    try:
        result = action()
    except Exception as exc:  # noqa: BLE001
        status.set(step_name, "failed")
        save_status(status_path, status)
        manifest.steps[step_name] = StepRecord(
            started_at=started_at,
            finished_at=utcnow_iso(),
            duration_seconds=round(perf_counter() - started_clock, 3),
            metadata={"error": str(exc)},
        )
        save_manifest(manifest_path, manifest)
        raise

    status.set(step_name, "completed")
    save_status(status_path, status)
    metadata = dict(result.get("metadata", {}))
    config_snapshot = step_config_snapshot(step_name, config)
    if config_snapshot is not None:
        metadata["config_snapshot"] = config_snapshot

    manifest.steps[step_name] = StepRecord(
        provider=result.get("provider"),
        started_at=started_at,
        finished_at=utcnow_iso(),
        duration_seconds=round(perf_counter() - started_clock, 3),
        inputs=dict(result.get("inputs", {})),
        outputs=dict(result.get("outputs", {})),
        metadata=metadata,
    )
    output_path = result.get("output_path")
    if output_path:
        manifest.outputs[step_name] = str(output_path)
    save_manifest(manifest_path, manifest)
