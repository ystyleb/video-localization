from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

from .models import AppConfig
from .utils import ensure_parent, module_available, run_command


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

    if audio_mode not in {"dub_only", "dub_plus_bgm"}:
        raise RuntimeError(f"Unsupported compose audio mode: {audio_mode}")

    background_audio_path: Path | None = None
    if audio_mode == "dub_plus_bgm":
        with tempfile.TemporaryDirectory(prefix="shorts-compose-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            source_audio_path = temp_dir / "source_audio.wav"
            _extract_audio_track(video_path, source_audio_path, config)

            if config.compose.enable_source_separation:
                background_audio_path = _separate_background_track(source_audio_path, temp_dir, config)
                effective_audio_mode = "dub_plus_bgm_separated"
            else:
                background_audio_path = source_audio_path
                effective_audio_mode = "dub_plus_bgm_unseparated"

            _compose_with_background_audio(
                video_path,
                voiceover_path,
                ass_path,
                background_audio_path,
                output_path,
                config,
            )
    else:
        _compose_with_voiceover_only(video_path, voiceover_path, ass_path, output_path, config)

    outputs = {"final_video": str(output_path)}
    if background_audio_path is not None:
        outputs["background_audio_path"] = str(background_audio_path)
    return {
        "output_path": str(output_path),
        "provider": "ffmpeg",
        "metadata": {
            "audio_mode": audio_mode,
            "effective_audio_mode": effective_audio_mode,
            "source_separation_provider": (
                config.compose.source_separation_provider
                if background_audio_path is not None and config.compose.enable_source_separation
                else None
            ),
            "bgm_gain_db": config.compose.bgm_gain_db if background_audio_path is not None else None,
        },
        "outputs": outputs,
        "inputs": {
            "video_path": str(video_path),
            "voiceover_path": str(voiceover_path),
            "ass_path": str(ass_path),
        },
    }


def _compose_with_voiceover_only(
    video_path: Path,
    voiceover_path: Path,
    ass_path: Path,
    output_path: Path,
    config: AppConfig,
) -> None:
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


def _compose_with_background_audio(
    video_path: Path,
    voiceover_path: Path,
    ass_path: Path,
    background_audio_path: Path,
    output_path: Path,
    config: AppConfig,
) -> None:
    filter_path = _escape_filter_path(ass_path)
    audio_filter = (
        "[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,volume=1[dub];"
        f"[2:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,volume={config.compose.bgm_gain_db}dB[bgm];"
        "[dub][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]"
    )
    cmd = [
        config.runtime.ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(voiceover_path),
        "-i",
        str(background_audio_path),
        "-vf",
        f"ass={filter_path}",
        "-filter_complex",
        audio_filter,
        "-map",
        "0:v:0",
        "-map",
        "[mix]",
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


def _extract_audio_track(video_path: Path, audio_path: Path, config: AppConfig) -> Path:
    ensure_parent(audio_path)
    cmd = [
        config.runtime.ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-c:a",
        "pcm_s16le",
        str(audio_path),
    ]
    run_command(cmd)
    return audio_path


def _separate_background_track(
    audio_path: Path,
    temp_dir: Path,
    config: AppConfig,
) -> Path:
    provider = config.compose.source_separation_provider
    if provider != "demucs":
        raise RuntimeError(f"Unsupported source separation provider: {provider}")
    if not module_available("demucs"):
        raise RuntimeError(
            "Source separation requires the `demucs` package. Install it with `uv --native-tls pip install demucs`."
        )
    if not module_available("torchcodec"):
        raise RuntimeError(
            "Demucs audio export requires the `torchcodec` package. Install it with `uv pip install torchcodec`."
        )

    output_root = temp_dir / "separated"
    cmd = [
        sys.executable,
        "-m",
        "demucs",
        "--two-stems=vocals",
        "-n",
        config.compose.source_separation_model,
        "-d",
        config.compose.source_separation_device,
        "-o",
        str(output_root),
        str(audio_path),
    ]
    run_command(cmd)

    candidate = output_root / config.compose.source_separation_model / audio_path.stem / "no_vocals.wav"
    if candidate.exists():
        return candidate

    matches = sorted(output_root.glob(f"**/{audio_path.stem}/no_vocals.wav"))
    if matches:
        return matches[0]

    raise RuntimeError("Demucs completed but no `no_vocals.wav` stem was produced")


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
