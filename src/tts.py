from __future__ import annotations

import copy
import glob
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from .models import AppConfig, MergedSegment, SubtitleSegment
from .subtitle import parse_srt
from .utils import (
    command_path,
    ffprobe_duration,
    module_available,
    project_root,
    render_shell_template,
    resolve_path,
    run_command,
)

KNOWN_VIBEVOICE_COMMANDS = ("python", "uv")
KNOWN_VOXCPM2_COMMANDS = ("voxcpm2", "voxcpm")
KNOWN_KOKORO_COMMANDS = ("kokoro",)
TERMINAL_PUNCTUATION_RE = re.compile(r"[.!?…][\"')\]]*$")
BROKEN_WORD_RE = re.compile(r"[A-Za-z0-9]-$")
WEAK_BREAK_RE = re.compile(r"[,;:—-][\"')\]]*$")
CONTINUATION_START_RE = re.compile(r"^(?:[a-z0-9\"'(]|—|-)")
DANGLING_END_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "because",
    "but",
    "by",
    "for",
    "from",
    "here",
    "if",
    "in",
    "is",
    "it's",
    "its",
    "let's",
    "my",
    "of",
    "on",
    "or",
    "our",
    "so",
    "that",
    "the",
    "their",
    "there",
    "then",
    "these",
    "this",
    "those",
    "to",
    "first",
    "was",
    "we're",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "why",
    "with",
    "your",
}


def apply_line_sync_tts_defaults(config: AppConfig) -> AppConfig:
    """Bias TTS toward one subtitle line per synthesis chunk."""
    config.tts.min_segment_chars = 0
    config.tts.merge_gap_ms = 0
    config.tts.sentence_aware_merge = False
    config.tts.smooth_merged_text = False
    return config


def generate_voiceover(
    en_srt: Path,
    output_wav: Path,
    config: AppConfig,
    *,
    source_audio: Path | None = None,
    zh_srt: Path | None = None,
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    segments = parse_srt(en_srt)
    if not segments:
        raise RuntimeError(f"No subtitle segments found in {en_srt}")

    effective_config = copy.deepcopy(config)
    tts_metadata: dict[str, Any] = {}
    if effective_config.tts.voice_mode == "clone":
        effective_config, tts_metadata = _prepare_config_for_clone_tts(
            effective_config,
            source_audio=source_audio,
            zh_srt=zh_srt,
            workspace_dir=workspace_dir,
        )

    merged_segments = merge_segments(
        segments,
        min_segment_chars=effective_config.tts.min_segment_chars,
        merge_gap_ms=effective_config.tts.merge_gap_ms,
        sentence_aware_merge=effective_config.tts.sentence_aware_merge,
        sentence_merge_max_duration_ms=effective_config.tts.sentence_merge_max_duration_ms,
        sentence_merge_max_chars=effective_config.tts.sentence_merge_max_chars,
    )
    merged_segments = _smooth_merged_segments_for_tts(merged_segments, effective_config)

    providers_used: list[str] = []
    provider_cache: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="shorts-tts-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        audio_parts: list[tuple[Path, int, int]] = []
        for position, segment in enumerate(merged_segments):
            raw_path = temp_dir / f"raw_{position:04d}.wav"
            provider_used = _synthesize_segment(
                segment.text,
                raw_path,
                effective_config,
                temp_dir,
                provider_cache,
            )
            providers_used.append(provider_used)

            aligned_path = temp_dir / f"aligned_{position:04d}.wav"
            _align_audio(raw_path, aligned_path, segment.end_ms - segment.start_ms, effective_config)
            audio_parts.append((aligned_path, segment.start_ms, segment.end_ms))

        timeline_path = temp_dir / "timeline.wav"
        total_duration_ms = merged_segments[-1].end_ms
        _assemble_timeline(audio_parts, timeline_path, total_duration_ms, effective_config, temp_dir)
        _normalize_audio(timeline_path, output_wav, effective_config)

    distinct_providers = list(dict.fromkeys(providers_used))
    metadata = {
        "merged_segment_count": len(merged_segments),
        "providers": distinct_providers,
        "smooth_merged_text": effective_config.tts.smooth_merged_text,
        "voice_mode": effective_config.tts.voice_mode,
    }
    metadata.update(tts_metadata)
    return {
        "output_path": str(output_wav),
        "provider": ",".join(distinct_providers),
        "metadata": metadata,
        "outputs": {"voiceover_wav": str(output_wav)},
        "inputs": {"en_srt": str(en_srt)},
    }


def merge_segments(
    segments: list[SubtitleSegment],
    *,
    min_segment_chars: int,
    merge_gap_ms: int,
    sentence_aware_merge: bool,
    sentence_merge_max_duration_ms: int,
    sentence_merge_max_chars: int,
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

        if _should_merge_segment_pair(
            current,
            segment,
            gap=gap,
            current_short=current_short,
            next_short=next_short,
            merge_gap_ms=merge_gap_ms,
            sentence_aware_merge=sentence_aware_merge,
            sentence_merge_max_duration_ms=sentence_merge_max_duration_ms,
            sentence_merge_max_chars=sentence_merge_max_chars,
        ):
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


def _should_merge_segment_pair(
    current: MergedSegment,
    next_segment: SubtitleSegment,
    *,
    gap: int,
    current_short: bool,
    next_short: bool,
    merge_gap_ms: int,
    sentence_aware_merge: bool,
    sentence_merge_max_duration_ms: int,
    sentence_merge_max_chars: int,
) -> bool:
    if gap > merge_gap_ms:
        return False

    candidate_duration_ms = next_segment.end_ms - current.start_ms
    candidate_text = f"{current.text.strip()} {next_segment.text.strip()}".strip()
    candidate_chars = len(candidate_text.replace(" ", ""))
    if candidate_duration_ms > sentence_merge_max_duration_ms:
        return False
    if candidate_chars > sentence_merge_max_chars:
        return False

    if current_short or next_short:
        return True

    if not sentence_aware_merge:
        return False

    return _boundary_needs_continuation(current.text, next_segment.text)


def _boundary_needs_continuation(current_text: str, next_text: str) -> bool:
    current_clean = current_text.strip()
    next_clean = next_text.strip()
    if not current_clean or not next_clean:
        return False

    if BROKEN_WORD_RE.search(current_clean):
        return True
    if WEAK_BREAK_RE.search(current_clean):
        return True
    if _ends_with_dangling_word(current_clean):
        return True
    if not TERMINAL_PUNCTUATION_RE.search(current_clean) and _count_words(current_clean) <= 6:
        return True
    if not TERMINAL_PUNCTUATION_RE.search(current_clean) and CONTINUATION_START_RE.search(next_clean):
        return True
    return False


def _ends_with_dangling_word(text: str) -> bool:
    matches = re.findall(r"[A-Za-z']+", text.lower())
    if not matches:
        return False
    return matches[-1] in DANGLING_END_WORDS


def _count_words(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def _smooth_merged_segments_for_tts(
    merged_segments: list[MergedSegment],
    config: AppConfig,
) -> list[MergedSegment]:
    if not config.tts.smooth_merged_text or len(merged_segments) <= 1:
        return merged_segments

    from .translate import smooth_spoken_english_chunks

    texts = [segment.text for segment in merged_segments]
    durations_ms = [segment.end_ms - segment.start_ms for segment in merged_segments]
    rewritten = smooth_spoken_english_chunks(texts, durations_ms, config)
    if len(rewritten) != len(merged_segments):
        return merged_segments

    smoothed: list[MergedSegment] = []
    for segment, rewritten_text in zip(merged_segments, rewritten, strict=True):
        smoothed.append(
            MergedSegment(
                indices=list(segment.indices),
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=rewritten_text.strip(),
            )
        )
    return smoothed


def _prepare_config_for_clone_tts(
    config: AppConfig,
    *,
    source_audio: Path | None,
    zh_srt: Path | None,
    workspace_dir: Path | None,
) -> tuple[AppConfig, dict[str, Any]]:
    clone_capable_providers = {"voxcpm2"}
    if config.tts.provider not in clone_capable_providers:
        if config.tts.fallback_provider in clone_capable_providers:
            config.tts.provider = str(config.tts.fallback_provider)
            config.tts.fallback_provider = None
        else:
            raise RuntimeError(
                "tts.voice_mode=clone requires a provider that accepts reference audio, "
                "for example `voxcpm2`."
            )

    if config.tts.fallback_provider and config.tts.fallback_provider not in clone_capable_providers:
        config.tts.fallback_provider = None

    provider_chain = _tts_provider_chain(config)
    if not any(provider in clone_capable_providers for provider in provider_chain):
        raise RuntimeError(
            "tts.voice_mode=clone requires a provider that accepts reference audio, "
            "for example `voxcpm2`."
        )

    metadata: dict[str, Any] = {"clone_reference_mode": "manual"}
    if config.tts.reference_wav:
        reference_wav = resolve_path(config.tts.reference_wav, project_root())
        if not reference_wav.exists():
            raise RuntimeError(f"Configured clone reference audio does not exist: {reference_wav}")
        config.tts.reference_wav = str(reference_wav)
        if config.tts.reference_text:
            config.tts.reference_text = config.tts.reference_text.strip()
        metadata["clone_reference_wav"] = str(reference_wav)
        if config.tts.reference_text:
            metadata["clone_reference_text_chars"] = len(config.tts.reference_text)
        elif not config.tts.auto_reference_from_source:
            raise RuntimeError(
                "tts.reference_text is required when tts.voice_mode=clone uses a manual reference_wav."
            )

    if not config.tts.reference_wav and config.tts.auto_reference_from_source:
        if source_audio is None or zh_srt is None or workspace_dir is None:
            raise RuntimeError(
                "Voice clone auto-reference requires source_audio, zh_srt, and workspace_dir."
            )
        from .voice_clone import prepare_reference_assets

        prepared = prepare_reference_assets(source_audio, zh_srt, workspace_dir, config)
        config.tts.reference_wav = str(prepared.wav_path)
        if not config.tts.reference_text:
            config.tts.reference_text = prepared.text
        metadata.update(
            {
                "clone_reference_mode": "auto",
                "clone_reference_wav": str(prepared.wav_path),
                "clone_reference_text_path": str(prepared.text_path),
                "clone_reference_text_chars": len(prepared.text),
                "clone_reference_start_ms": prepared.start_ms,
                "clone_reference_end_ms": prepared.end_ms,
                "clone_reference_segment_indices": prepared.indices,
                "clone_reference_used_vocals_stem": prepared.used_vocals_stem,
            }
        )

    if not config.tts.reference_wav:
        raise RuntimeError(
            "tts.voice_mode=clone requires either tts.reference_wav or auto_reference_from_source."
        )

    if not (config.tts.reference_text or "").strip():
        raise RuntimeError(
            "tts.voice_mode=clone requires reference_text. "
            "Provide tts.reference_text or enable auto reference extraction."
        )

    config.tts.reference_text = str(config.tts.reference_text).strip()
    return config, metadata


def _synthesize_segment(
    text: str,
    output_path: Path,
    config: AppConfig,
    temp_dir: Path,
    provider_cache: dict[str, Any],
) -> str:
    attempts: list[str] = []
    for provider_name in _tts_provider_chain(config):
        try:
            if provider_name == "vibevoice_realtime":
                _synthesize_with_vibevoice_realtime(
                    text,
                    output_path,
                    config,
                    temp_dir,
                    provider_cache,
                )
            elif provider_name == "voxcpm2":
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
    template = template or _default_tts_command_template(provider_name)
    if not template:
        known_commands = (
            KNOWN_VIBEVOICE_COMMANDS
            if provider_name == "vibevoice_realtime"
            else KNOWN_VOXCPM2_COMMANDS
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
            "output_dir": output_path.parent,
            "sample_rate": config.tts.sample_rate,
            "voice_description": config.tts.voice_description,
            "reference_wav": config.tts.reference_wav or "",
            "reference_text": config.tts.reference_text or "",
            "model_path": config.tts.vibevoice_model_path,
            "repo_dir": config.tts.vibevoice_repo_dir or "",
            "voice_prompt_pt": config.tts.vibevoice_voice_prompt_pt or "",
            "speaker_name": config.tts.vibevoice_speaker_name,
            "device": config.tts.vibevoice_device,
            "cfg_scale": config.tts.vibevoice_cfg_scale,
            "python_bin": sys.executable,
            "voxcpm2_base_url": config.tts.voxcpm2_base_url,
            "voxcpm2_runner": project_root() / "scripts" / "voxcpm_http_tts.py",
            "voxcpm_hf_model_id": config.tts.voxcpm_hf_model_id,
        },
    )
    run_command(command)
    if not output_path.exists():
        raise RuntimeError("TTS command completed but output audio was not created")


def _default_tts_command_template(provider_name: str) -> str | None:
    if provider_name != "voxcpm2":
        return None
    if command_path("voxcpm"):
        return (
            "voxcpm clone "
            "--hf-model-id {voxcpm_hf_model_id} "
            "--text {text} "
            "--prompt-audio {reference_wav} "
            "--prompt-text {reference_text} "
            "--output {output_path} "
            "--no-denoiser "
            "--no-optimize"
        )

    runner = project_root() / "scripts" / "voxcpm_http_tts.py"
    if runner.exists():
        return (
            "{python_bin} {voxcpm2_runner} "
            "--base-url {voxcpm2_base_url} "
            "--text-file {text_file} "
            "--output {output_path} "
            "--prompt-wav-path {reference_wav} "
            "--prompt-text {reference_text}"
        )
    return None


def _synthesize_with_vibevoice_realtime(
    text: str,
    output_path: Path,
    config: AppConfig,
    temp_dir: Path,
    provider_cache: dict[str, Any],
) -> None:
    if module_available("vibevoice") and module_available("torch"):
        runtime = provider_cache.get("vibevoice_realtime")
        if runtime is None:
            runtime = _load_vibevoice_runtime(config)
            provider_cache["vibevoice_realtime"] = runtime
        _generate_with_vibevoice_runtime(runtime, text, output_path)
        return

    if config.tts.vibevoice_realtime_command:
        _synthesize_with_command(
            config.tts.vibevoice_realtime_command,
            text,
            output_path,
            config,
            temp_dir,
            provider_name="vibevoice_realtime",
        )
        return

    raise RuntimeError(
        "VibeVoice-Realtime requires either the official `vibevoice` Python package "
        "to be installed or `tts.vibevoice_realtime_command` to be configured."
    )


def _load_vibevoice_runtime(config: AppConfig) -> dict[str, Any]:
    import torch
    from vibevoice.modular.modeling_vibevoice_streaming_inference import (
        VibeVoiceStreamingForConditionalGenerationInference,
    )
    from vibevoice.processor.vibevoice_streaming_processor import VibeVoiceStreamingProcessor

    device = _pick_vibevoice_device(config)
    dtype = torch.float32 if device in {"cpu", "mps"} else torch.bfloat16
    attn_implementation = "sdpa" if device in {"cpu", "mps"} else "flash_attention_2"
    device_map = None if device == "mps" else device

    processor = VibeVoiceStreamingProcessor.from_pretrained(config.tts.vibevoice_model_path)
    try:
        model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
            config.tts.vibevoice_model_path,
            torch_dtype=dtype,
            attn_implementation=attn_implementation,
            device_map=device_map,
        )
    except Exception:  # noqa: BLE001
        model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
            config.tts.vibevoice_model_path,
            torch_dtype=dtype,
            attn_implementation="sdpa",
            device_map=device_map,
        )

    if device == "mps":
        model = model.to(device)
    model.eval()
    model.set_ddpm_inference_steps(num_steps=5)

    voice_prompt_path = _resolve_vibevoice_voice_prompt(config)
    cached_prompt = torch.load(voice_prompt_path, map_location=device, weights_only=False)
    return {
        "cfg_scale": config.tts.vibevoice_cfg_scale,
        "cached_prompt": cached_prompt,
        "device": device,
        "model": model,
        "processor": processor,
    }


def _generate_with_vibevoice_runtime(
    runtime: dict[str, Any],
    text: str,
    output_path: Path,
) -> None:
    processor = runtime["processor"]
    model = runtime["model"]
    cached_prompt = runtime["cached_prompt"]
    device = runtime["device"]

    inputs = processor.process_input_with_cached_prompt(
        text=text,
        cached_prompt=cached_prompt,
        padding=True,
        return_tensors="pt",
        return_attention_mask=True,
    )
    prepared_inputs = {
        key: value.to(device) if hasattr(value, "to") else value
        for key, value in inputs.items()
    }
    outputs = model.generate(
        **prepared_inputs,
        max_new_tokens=None,
        cfg_scale=runtime["cfg_scale"],
        tokenizer=processor.tokenizer,
        generation_config={"do_sample": False},
        verbose=False,
        all_prefilled_outputs=copy.deepcopy(cached_prompt),
    )
    processor.save_audio(outputs.speech_outputs[0], output_path=str(output_path))
    if not output_path.exists():
        raise RuntimeError("VibeVoice-Realtime did not create the expected output audio file")


def _pick_vibevoice_device(config: AppConfig) -> str:
    import torch

    preferred = (config.tts.vibevoice_device or "auto").lower()
    if preferred != "auto":
        return preferred
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _resolve_vibevoice_voice_prompt(config: AppConfig) -> Path:
    if config.tts.vibevoice_voice_prompt_pt:
        path = resolve_path(config.tts.vibevoice_voice_prompt_pt, project_root())
        if path.exists():
            return path
        raise RuntimeError(f"Configured VibeVoice voice prompt does not exist: {path}")

    if config.tts.vibevoice_repo_dir:
        repo_dir = resolve_path(config.tts.vibevoice_repo_dir, project_root())
        voice_dir = repo_dir / "demo" / "voices" / "streaming_model"
        if not voice_dir.exists():
            raise RuntimeError(f"VibeVoice repo voice directory not found: {voice_dir}")
        matches = sorted(glob.glob(str(voice_dir / f"*{config.tts.vibevoice_speaker_name}*.pt")))
        if not matches:
            matches = sorted(glob.glob(str(voice_dir / "*.pt")))
        if not matches:
            raise RuntimeError(f"No VibeVoice prompt `.pt` files found in {voice_dir}")
        return Path(matches[0])

    raise RuntimeError(
        "Configure either `tts.vibevoice_voice_prompt_pt` or `tts.vibevoice_repo_dir` "
        "to let VibeVoice-Realtime find a voice prompt preset."
    )


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
