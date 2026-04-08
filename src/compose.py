from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import AppConfig
from .utils import ensure_parent, run_command


def burn_subtitles(
    video_path: Path,
    ass_path: Path,
    output_path: Path,
    config: AppConfig,
) -> Path:
    ensure_parent(output_path)
    filter_path = _escape_filter_path(ass_path)
    cmd = [
        config.runtime.ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"ass={filter_path}",
        "-c:v",
        config.compose.video_codec,
        "-preset",
        config.compose.preset,
        "-crf",
        str(config.compose.crf),
        "-c:a",
        "copy",
        str(output_path),
    ]
    run_command(cmd)
    return output_path


def compose_video(
    video_path: Path,
    voiceover_path: Path,
    ass_path: Path,
    output_path: Path,
    config: AppConfig,
) -> dict[str, Any]:
    ensure_parent(output_path)
    audio_mode = config.compose.audio_mode
    effective_audio_mode = audio_mode

    if audio_mode == "dub_plus_bgm" and config.compose.enable_source_separation:
        raise RuntimeError(
            "dub_plus_bgm with source separation is reserved for a later phase; "
            "switch compose.audio_mode to dub_only for MVP"
        )

    if audio_mode not in {"dub_only", "dub_plus_bgm"}:
        raise RuntimeError(f"Unsupported compose audio mode: {audio_mode}")

    if audio_mode == "dub_plus_bgm" and not config.compose.enable_source_separation:
        effective_audio_mode = "dub_only"

    filter_path = _escape_filter_path(ass_path)
    cmd = [
        config.runtime.ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(voiceover_path),
        "-vf",
        f"ass={filter_path}",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        config.compose.video_codec,
        "-preset",
        config.compose.preset,
        "-crf",
        str(config.compose.crf),
        "-c:a",
        config.compose.audio_codec,
        "-b:a",
        config.compose.audio_bitrate,
        "-shortest",
        str(output_path),
    ]
    run_command(cmd)
    return {
        "output_path": str(output_path),
        "provider": "ffmpeg",
        "metadata": {"audio_mode": audio_mode, "effective_audio_mode": effective_audio_mode},
        "outputs": {"final_video": str(output_path)},
        "inputs": {
            "video_path": str(video_path),
            "voiceover_path": str(voiceover_path),
            "ass_path": str(ass_path),
        },
    }


def _escape_filter_path(path: Path) -> str:
    value = path.as_posix()
    return (
        value.replace("\\", "\\\\")
        .replace(":", r"\:")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
        .replace("'", r"\'")
    )
