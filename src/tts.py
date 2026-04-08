from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .models import AppConfig, MergedSegment, SubtitleSegment
from .subtitle import parse_srt
from .utils import command_path, ffprobe_duration, render_shell_template, run_command

KNOWN_VOXCPM2_COMMANDS = ("voxcpm2", "voxcpm")
KNOWN_KOKORO_COMMANDS = ("kokoro",)


def generate_voiceover(en_srt: Path, output_wav: Path, config: AppConfig) -> dict[str, Any]:
    segments = parse_srt(en_srt)
    if not segments:
        raise RuntimeError(f"No subtitle segments found in {en_srt}")

    merged_segments = merge_segments(
        segments,
        min_segment_chars=config.tts.min_segment_chars,
        merge_gap_ms=config.tts.merge_gap_ms,
    )

    providers_used: list[str] = []
    with tempfile.TemporaryDirectory(prefix="shorts-tts-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        audio_parts: list[tuple[Path, int, int]] = []
        for position, segment in enumerate(merged_segments):
            raw_path = temp_dir / f"raw_{position:04d}.wav"
            provider_used = _synthesize_segment(segment.text, raw_path, config, temp_dir)
            providers_used.append(provider_used)

            aligned_path = temp_dir / f"aligned_{position:04d}.wav"
            _align_audio(raw_path, aligned_path, segment.end_ms - segment.start_ms, config)
            audio_parts.append((aligned_path, segment.start_ms, segment.end_ms))

        timeline_path = temp_dir / "timeline.wav"
        total_duration_ms = merged_segments[-1].end_ms
        _assemble_timeline(audio_parts, timeline_path, total_duration_ms, config, temp_dir)
        _normalize_audio(timeline_path, output_wav, config)

    distinct_providers = list(dict.fromkeys(providers_used))
    return {
        "output_path": str(output_wav),
        "provider": ",".join(distinct_providers),
        "metadata": {
            "merged_segment_count": len(merged_segments),
            "providers": distinct_providers,
        },
        "outputs": {"voiceover_wav": str(output_wav)},
        "inputs": {"en_srt": str(en_srt)},
    }


def merge_segments(
    segments: list[SubtitleSegment],
    *,
    min_segment_chars: int,
    merge_gap_ms: int,
) -> list[MergedSegment]:
    merged: list[MergedSegment] = []
    current: MergedSegment | None = None

    for segment in segments:
        text_length = len(segment.text.replace(" ", ""))
        if current is None:
            current = MergedSegment(
                indices=[segment.index],
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=segment.text.strip(),
            )
            continue

        gap = max(segment.start_ms - current.end_ms, 0)
        current_short = len(current.text.replace(" ", "")) < min_segment_chars
        next_short = text_length < min_segment_chars

        if (current_short or next_short) and gap <= merge_gap_ms:
            current.indices.append(segment.index)
            current.end_ms = segment.end_ms
            current.text = f"{current.text} {segment.text.strip()}".strip()
        else:
            merged.append(current)
            current = MergedSegment(
                indices=[segment.index],
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=segment.text.strip(),
            )

    if current is not None:
        merged.append(current)

    return merged


def _synthesize_segment(
    text: str,
    output_path: Path,
    config: AppConfig,
    temp_dir: Path,
) -> str:
    attempts: list[str] = []
    for provider_name in _tts_provider_chain(config):
        try:
            if provider_name == "voxcpm2":
                _synthesize_with_command(
                    config.tts.voxcpm2_command,
                    text,
                    output_path,
                    config,
                    temp_dir,
                    provider_name="voxcpm2",
                )
            elif provider_name == "kokoro":
                _synthesize_with_command(
                    config.tts.kokoro_command,
                    text,
                    output_path,
                    config,
                    temp_dir,
                    provider_name="kokoro",
                )
            elif provider_name == "macos_say":
                _synthesize_with_macos_say(text, output_path, config, temp_dir)
            else:
                raise RuntimeError(f"Unsupported TTS provider: {provider_name}")
            return provider_name
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"{provider_name}: {exc}")

    joined_errors = "; ".join(attempts) or "no providers attempted"
    raise RuntimeError(f"TTS failed for segment: {joined_errors}")


def _tts_provider_chain(config: AppConfig) -> list[str]:
    providers = [config.tts.provider, config.tts.fallback_provider]
    seen: set[str] = set()
    ordered: list[str] = []
    for provider in providers:
        if provider and provider not in seen:
            ordered.append(provider)
            seen.add(provider)
    return ordered


def _synthesize_with_command(
    template: str | None,
    text: str,
    output_path: Path,
    config: AppConfig,
    temp_dir: Path,
    *,
    provider_name: str,
) -> None:
    if not template:
        known_commands = (
            KNOWN_VOXCPM2_COMMANDS
            if provider_name == "voxcpm2"
            else KNOWN_KOKORO_COMMANDS
            if provider_name == "kokoro"
            else ()
        )
        detected = [name for name in known_commands if command_path(name)]
        detected_hint = (
            f" Detected local candidates: {', '.join(detected)}."
            if detected
            else " No matching executable was detected on PATH."
        )
        raise RuntimeError(
            f"Missing `tts.{provider_name}_command` in config.yaml. "
            "It should accept `{text}` or `{text_file}` plus `{output_path}`."
            f"{detected_hint}"
        )

    text_file = temp_dir / f"{output_path.stem}.txt"
    text_file.write_text(text, encoding="utf-8")
    command = render_shell_template(
        template,
        {
            "text": text,
            "text_file": text_file,
            "output_path": output_path,
            "sample_rate": config.tts.sample_rate,
            "voice_description": config.tts.voice_description,
            "reference_wav": config.tts.reference_wav or "",
        },
    )
    run_command(command)
    if not output_path.exists():
        raise RuntimeError("TTS command completed but output audio was not created")


def _synthesize_with_macos_say(
    text: str,
    output_path: Path,
    config: AppConfig,
    temp_dir: Path,
) -> None:
    text_file = temp_dir / f"{output_path.stem}.txt"
    aiff_path = temp_dir / f"{output_path.stem}.aiff"
    text_file.write_text(text, encoding="utf-8")
    run_command(
        [
            "say",
            "-v",
            config.tts.macos_voice,
            "-o",
            str(aiff_path),
            "-f",
            str(text_file),
        ]
    )
    run_command(
        [
            config.runtime.ffmpeg_bin,
            "-y",
            "-i",
            str(aiff_path),
            "-ac",
            "1",
            "-ar",
            str(config.tts.sample_rate),
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )


def _align_audio(raw_path: Path, aligned_path: Path, target_ms: int, config: AppConfig) -> None:
    if target_ms <= 0:
        run_command(
            [
                config.runtime.ffmpeg_bin,
                "-y",
                "-i",
                str(raw_path),
                "-ac",
                "1",
                "-ar",
                str(config.tts.sample_rate),
                "-c:a",
                "pcm_s16le",
                str(aligned_path),
            ]
        )
        return

    actual_ms = int(ffprobe_duration(raw_path, config) * 1000)
    filters: list[str] = []

    if actual_ms > target_ms:
        tempo = min(actual_ms / target_ms, config.tts.max_tempo)
        filters.append(f"atempo={tempo:.5f}")

    target_seconds = target_ms / 1000
    filters.append(f"apad=pad_dur={target_seconds:.5f}")
    filters.append(f"atrim=duration={target_seconds:.5f}")

    run_command(
        [
            config.runtime.ffmpeg_bin,
            "-y",
            "-i",
            str(raw_path),
            "-af",
            ",".join(filters),
            "-ac",
            "1",
            "-ar",
            str(config.tts.sample_rate),
            "-c:a",
            "pcm_s16le",
            str(aligned_path),
        ]
    )


def _assemble_timeline(
    audio_parts: list[tuple[Path, int, int]],
    output_path: Path,
    total_duration_ms: int,
    config: AppConfig,
    temp_dir: Path,
) -> None:
    concat_files: list[Path] = []
    cursor_ms = 0
    for index, (audio_path, start_ms, end_ms) in enumerate(audio_parts):
        if start_ms > cursor_ms:
            silence_path = temp_dir / f"silence_{index:04d}.wav"
            _generate_silence(silence_path, start_ms - cursor_ms, config)
            concat_files.append(silence_path)
        concat_files.append(audio_path)
        cursor_ms = end_ms

    if total_duration_ms > cursor_ms:
        silence_path = temp_dir / "silence_final.wav"
        _generate_silence(silence_path, total_duration_ms - cursor_ms, config)
        concat_files.append(silence_path)

    concat_manifest = temp_dir / "concat.txt"
    concat_manifest.write_text(
        "\n".join(f"file '{path.as_posix()}'" for path in concat_files) + "\n",
        encoding="utf-8",
    )

    run_command(
        [
            config.runtime.ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_manifest),
            "-c",
            "copy",
            str(output_path),
        ]
    )


def _generate_silence(path: Path, duration_ms: int, config: AppConfig) -> None:
    run_command(
        [
            config.runtime.ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=mono:sample_rate={config.tts.sample_rate}",
            "-t",
            f"{duration_ms / 1000:.5f}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )


def _normalize_audio(source_path: Path, output_path: Path, config: AppConfig) -> None:
    run_command(
        [
            config.runtime.ffmpeg_bin,
            "-y",
            "-i",
            str(source_path),
            "-af",
            f"loudnorm=I={config.tts.normalize_lufs}:TP=-1.5:LRA=11",
            "-ac",
            "1",
            "-ar",
            str(config.tts.sample_rate),
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )
