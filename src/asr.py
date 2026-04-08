from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .models import AppConfig, SubtitleSegment
from .subtitle import parse_srt, write_srt
from .utils import (
    command_path,
    ensure_parent,
    ffprobe_duration,
    module_available,
    render_shell_template,
    run_command,
)

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
    if module_available("qwen_asr") and module_available("torch"):
        return _transcribe_with_qwen3_python(audio_path, config)

    if not config.asr.qwen3_command:
        detected = [name for name in KNOWN_QWEN3_COMMANDS if command_path(name)]
        detected_hint = (
            f" Detected local candidates: {', '.join(detected)}."
            if detected
            else " No known Qwen/FunASR executable was detected on PATH, and the `qwen_asr` Python package is not available."
        )
        raise RuntimeError(
            "qwen3_asr provider requires `asr.qwen3_command` in config.yaml. "
            "It should be a shell template that accepts `{audio_path}` and `{output_srt}`, "
            "or install the official `qwen-asr` package into the project environment."
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


def _transcribe_with_qwen3_python(audio_path: Path, config: AppConfig) -> list[SubtitleSegment]:
    import torch
    from qwen_asr import Qwen3ASRModel

    device = _pick_qwen_device(config)
    dtype = _torch_dtype(torch, config.asr.qwen3_dtype)
    attn_implementation = config.asr.qwen3_attn_implementation or "sdpa"
    device_map = None if device == "mps" else device

    model = Qwen3ASRModel.from_pretrained(
        config.asr.qwen3_model_id,
        dtype=dtype,
        device_map=device_map,
        attn_implementation=attn_implementation,
        max_new_tokens=config.asr.qwen3_max_new_tokens,
        forced_aligner=config.asr.qwen3_forced_aligner_id,
        forced_aligner_kwargs={
            "dtype": dtype,
            "device_map": device_map,
            "attn_implementation": attn_implementation,
        },
    )
    if device == "mps":
        _move_qwen_runtime_to_device(model, device)

    results = model.transcribe(
        audio=str(audio_path),
        language=_qwen_language_name(config.asr.language),
        return_time_stamps=True,
    )
    result = results[0] if isinstance(results, list) and results else results
    timestamp_units = _extract_timestamp_units(result)
    if timestamp_units:
        segments = _segments_from_timestamp_units(timestamp_units, config)
        if segments:
            return segments

    text = str(_get_result_field(result, "text") or "").strip()
    if not text:
        raise RuntimeError("Qwen3-ASR returned no text and no timestamp units")

    duration_ms = int(ffprobe_duration(audio_path, config) * 1000)
    return [
        SubtitleSegment(
            index=1,
            start_ms=0,
            end_ms=max(duration_ms, 1_000),
            text=text,
        )
    ]


def _move_qwen_runtime_to_device(runtime: object, device: str) -> None:
    target = getattr(runtime, "model", None)
    if target is not None and hasattr(target, "to"):
        runtime.model = target.to(device)
    if hasattr(runtime, "device"):
        runtime.device = device

    forced_aligner = getattr(runtime, "forced_aligner", None)
    forced_model = getattr(forced_aligner, "model", None)
    if forced_model is not None and hasattr(forced_model, "to"):
        forced_aligner.model = forced_model.to(device)
    if forced_aligner is not None and hasattr(forced_aligner, "device"):
        forced_aligner.device = device


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


def _pick_qwen_device(config: AppConfig) -> str:
    import torch

    preferred = (config.asr.qwen3_device or "auto").lower()
    if preferred != "auto":
        return preferred
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _torch_dtype(torch_module: object, dtype_name: str) -> object:
    normalized = (dtype_name or "float32").lower()
    mapping = {
        "float16": "float16",
        "fp16": "float16",
        "bfloat16": "bfloat16",
        "bf16": "bfloat16",
        "float32": "float32",
        "fp32": "float32",
    }
    attribute = mapping.get(normalized, "float32")
    return getattr(torch_module, attribute)


def _qwen_language_name(language: str) -> str:
    mapping = {
        "zh": "Chinese",
        "zh-cn": "Chinese",
        "cmn": "Chinese",
        "en": "English",
    }
    return mapping.get(language.lower(), language)


def _extract_timestamp_units(result: object) -> list[dict[str, Any]]:
    raw = _get_result_field(result, "time_stamps") or _get_result_field(result, "timestamps")
    if raw is None:
        return []

    if isinstance(raw, list) and raw and isinstance(raw[0], list):
        raw = raw[0]
    elif hasattr(raw, "items"):
        raw = getattr(raw, "items")

    units: list[dict[str, Any]] = []
    for item in _iterate_timestamp_items(raw):
        text = str(_get_result_field(item, "text") or "").strip()
        start = _coerce_ms(_get_result_field(item, "start_time") or _get_result_field(item, "start"))
        end = _coerce_ms(_get_result_field(item, "end_time") or _get_result_field(item, "end"))
        if not text or start is None or end is None:
            continue
        units.append({"text": text, "start_ms": start, "end_ms": end})
    return units


def _iterate_timestamp_items(raw: object) -> Iterable[object]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, tuple):
        return list(raw)
    if hasattr(raw, "__iter__") and not isinstance(raw, (str, bytes, dict)):
        return list(raw)
    return []


def _coerce_ms(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 1_000 else value * 1_000
    if isinstance(value, float):
        return int(value if value > 1_000 else value * 1_000)
    return None


def _get_result_field(result: object, field_name: str) -> Any:
    if isinstance(result, dict):
        return result.get(field_name)
    return getattr(result, field_name, None)


def _segments_from_timestamp_units(
    units: list[dict[str, Any]],
    config: AppConfig,
) -> list[SubtitleSegment]:
    segments: list[SubtitleSegment] = []
    buffer: list[dict[str, Any]] = []

    def flush() -> None:
        if not buffer:
            return
        text = "".join(str(item["text"]) for item in buffer).strip()
        if not text:
            buffer.clear()
            return
        segments.append(
            SubtitleSegment(
                index=len(segments) + 1,
                start_ms=int(buffer[0]["start_ms"]),
                end_ms=int(buffer[-1]["end_ms"]),
                text=text,
            )
        )
        buffer.clear()

    for item in units:
        buffer.append(item)
        text = "".join(str(entry["text"]) for entry in buffer).strip()
        duration_ms = int(buffer[-1]["end_ms"]) - int(buffer[0]["start_ms"])
        if (
            len(text) >= config.asr.qwen3_max_segment_chars
            or duration_ms >= config.asr.qwen3_max_segment_ms
            or text.endswith(("。", "！", "？", "；", ".", "!", "?", ";"))
        ):
            flush()

    flush()
    return segments
