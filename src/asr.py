from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import AppConfig, SubtitleSegment
from .subtitle import parse_srt, write_srt
from .utils import command_path, ensure_parent, module_available, render_shell_template, run_command

KNOWN_QWEN3_COMMANDS = ("qwen3-asr", "qwen", "funasr")


def extract_audio(video_path: Path, audio_path: Path, config: AppConfig) -> Path:
    ensure_parent(audio_path)
    cmd = [
        config.runtime.ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        str(config.asr.channels),
        "-ar",
        str(config.asr.sample_rate),
        "-c:a",
        "pcm_s16le",
        str(audio_path),
    ]
    run_command(cmd)
    return audio_path


def transcribe(
    video_path: Path,
    audio_path: Path,
    output_srt: Path,
    config: AppConfig,
) -> dict[str, Any]:
    extract_audio(video_path, audio_path, config)

    errors: list[str] = []
    segments: list[SubtitleSegment] | None = None
    used_provider: str | None = None

    for provider_name in _provider_chain(config):
        try:
            if provider_name == "qwen3_asr":
                segments = _transcribe_with_qwen3(audio_path, output_srt, config)
            elif provider_name == "faster_whisper":
                segments = _transcribe_with_faster_whisper(audio_path, config)
            else:
                raise RuntimeError(f"Unsupported ASR provider: {provider_name}")
            used_provider = provider_name
            break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{provider_name}: {exc}")

    if not segments or used_provider is None:
        joined_errors = "; ".join(errors) or "no providers attempted"
        raise RuntimeError(f"ASR failed for {video_path.name}: {joined_errors}")

    write_srt(segments, output_srt)
    return {
        "output_path": str(output_srt),
        "provider": used_provider,
        "metadata": {"segment_count": len(segments), "attempts": errors},
        "outputs": {"audio_path": str(audio_path), "zh_srt": str(output_srt)},
        "inputs": {"video_path": str(video_path)},
    }


def _provider_chain(config: AppConfig) -> list[str]:
    providers = [config.asr.provider, config.asr.fallback_provider]
    seen: set[str] = set()
    ordered: list[str] = []
    for provider in providers:
        if provider and provider not in seen:
            ordered.append(provider)
            seen.add(provider)
    return ordered


def _transcribe_with_qwen3(
    audio_path: Path,
    output_srt: Path,
    config: AppConfig,
) -> list[SubtitleSegment]:
    if not config.asr.qwen3_command:
        detected = [name for name in KNOWN_QWEN3_COMMANDS if command_path(name)]
        detected_hint = (
            f" Detected local candidates: {', '.join(detected)}."
            if detected
            else " No known Qwen/FunASR executable was detected on PATH."
        )
        raise RuntimeError(
            "qwen3_asr provider requires `asr.qwen3_command` in config.yaml. "
            "It should be a shell template that accepts `{audio_path}` and `{output_srt}`."
            f"{detected_hint}"
        )
    cmd = render_shell_template(
        config.asr.qwen3_command,
        {"audio_path": audio_path, "output_srt": output_srt},
    )
    run_command(cmd)
    if not output_srt.exists():
        raise RuntimeError("qwen3_asr command completed but did not create output SRT")
    return parse_srt(output_srt)


def _transcribe_with_faster_whisper(audio_path: Path, config: AppConfig) -> list[SubtitleSegment]:
    if not module_available("faster_whisper"):
        raise RuntimeError(
            "faster-whisper is not installed. Run `uv add faster-whisper` or "
            "`uv sync --extra asr` before using the fallback provider."
        )

    from faster_whisper import WhisperModel

    model = WhisperModel(
        config.asr.faster_whisper_model,
        device=config.asr.device,
        compute_type=config.asr.compute_type,
    )
    raw_segments, _ = model.transcribe(
        str(audio_path),
        language=config.asr.language,
        vad_filter=True,
    )

    segments: list[SubtitleSegment] = []
    for index, segment in enumerate(raw_segments, start=1):
        text = (segment.text or "").strip()
        if not text:
            continue
        segments.append(
            SubtitleSegment(
                index=index,
                start_ms=int(segment.start * 1000),
                end_ms=int(segment.end * 1000),
                text=text,
            )
        )
    return segments
