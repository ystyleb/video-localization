from __future__ import annotations

import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .models import AppConfig, SubtitleSegment
from .subtitle import parse_srt
from .utils import ensure_parent, module_available, run_command

NON_CONTENT_RE = re.compile(r"[\s\W_]+", re.UNICODE)


@dataclass(slots=True)
class ReferenceCandidate:
    indices: list[int]
    start_ms: int
    end_ms: int
    text: str
    score: float


@dataclass(slots=True)
class PreparedReference:
    wav_path: Path
    text_path: Path
    text: str
    start_ms: int
    end_ms: int
    used_vocals_stem: bool
    indices: list[int]


def prepare_reference_assets(
    source_audio_path: Path,
    zh_srt_path: Path,
    workspace_dir: Path,
    config: AppConfig,
) -> PreparedReference:
    segments = parse_srt(zh_srt_path)
    if not segments:
        raise RuntimeError(f"No subtitle segments found in {zh_srt_path} for voice cloning")

    candidate = _pick_reference_candidate(segments, config)
    if candidate is None:
        raise RuntimeError("Unable to find a usable subtitle span for voice cloning")

    reference_audio_source = source_audio_path
    used_vocals_stem = False
    if config.tts.auto_reference_use_vocals:
        try:
            reference_audio_source = _extract_vocals_stem(source_audio_path, workspace_dir, config)
            used_vocals_stem = True
        except Exception:
            reference_audio_source = source_audio_path
            used_vocals_stem = False

    reference_wav_path = workspace_dir / "clone_reference.wav"
    reference_text_path = workspace_dir / "clone_reference.txt"
    _extract_audio_clip(
        reference_audio_source,
        reference_wav_path,
        candidate.start_ms,
        candidate.end_ms,
        config,
    )
    ensure_parent(reference_text_path)
    reference_text_path.write_text(candidate.text.strip() + "\n", encoding="utf-8")

    return PreparedReference(
        wav_path=reference_wav_path,
        text_path=reference_text_path,
        text=candidate.text.strip(),
        start_ms=candidate.start_ms,
        end_ms=candidate.end_ms,
        used_vocals_stem=used_vocals_stem,
        indices=list(candidate.indices),
    )


def _pick_reference_candidate(
    segments: list[SubtitleSegment],
    config: AppConfig,
) -> ReferenceCandidate | None:
    candidates: list[ReferenceCandidate] = []
    min_chars = config.tts.auto_reference_min_chars
    min_duration_ms = config.tts.auto_reference_min_duration_ms
    target_duration_ms = config.tts.auto_reference_target_duration_ms
    max_duration_ms = config.tts.auto_reference_max_duration_ms
    max_gap_ms = config.tts.auto_reference_max_gap_ms

    for start_index, start_segment in enumerate(segments):
        text_parts: list[str] = []
        indices: list[int] = []
        current_start_ms = start_segment.start_ms
        current_end_ms = start_segment.end_ms

        for offset, segment in enumerate(segments[start_index:], start=0):
            if offset > 0 and segment.start_ms - current_end_ms > max_gap_ms:
                break

            candidate_end_ms = segment.end_ms
            duration_ms = candidate_end_ms - current_start_ms
            if duration_ms > max_duration_ms:
                break

            current_end_ms = candidate_end_ms
            indices.append(segment.index)
            text_parts.append(segment.text.strip())
            merged_text = _join_text_parts(text_parts)
            if not merged_text:
                continue

            content_chars = len(NON_CONTENT_RE.sub("", merged_text))
            if duration_ms < min_duration_ms or content_chars < min_chars:
                continue

            score = (
                content_chars * 8
                - abs(duration_ms - target_duration_ms) / 100
                - max(len(indices) - 1, 0) * 0.25
            )
            candidates.append(
                ReferenceCandidate(
                    indices=list(indices),
                    start_ms=current_start_ms,
                    end_ms=current_end_ms,
                    text=merged_text,
                    score=score,
                )
            )

    if candidates:
        candidates.sort(key=lambda item: (-item.score, item.start_ms))
        return candidates[0]

    fallback_segments = sorted(
        (segment for segment in segments if segment.text.strip()),
        key=lambda item: (item.end_ms - item.start_ms, len(NON_CONTENT_RE.sub("", item.text))),
        reverse=True,
    )
    if not fallback_segments:
        return None

    segment = fallback_segments[0]
    return ReferenceCandidate(
        indices=[segment.index],
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        text=segment.text.strip(),
        score=0.0,
    )


def _join_text_parts(parts: list[str]) -> str:
    joined: list[str] = []
    previous_tail = ""
    for raw_part in parts:
        part = re.sub(r"\s+", " ", raw_part).strip()
        if not part:
            continue
        if joined and _needs_space(previous_tail, part):
            joined.append(" ")
        joined.append(part)
        previous_tail = part
    return "".join(joined).strip()


def _needs_space(previous_part: str, next_part: str) -> bool:
    if not previous_part or not next_part:
        return False
    previous_char = previous_part[-1]
    next_char = next_part[0]
    return previous_char.isascii() and next_char.isascii() and previous_char.isalnum() and next_char.isalnum()


def _extract_audio_clip(
    source_audio_path: Path,
    output_path: Path,
    start_ms: int,
    end_ms: int,
    config: AppConfig,
) -> Path:
    ensure_parent(output_path)
    duration_ms = max(end_ms - start_ms, 1000)
    cmd = [
        config.runtime.ffmpeg_bin,
        "-y",
        "-ss",
        f"{start_ms / 1000:.3f}",
        "-t",
        f"{duration_ms / 1000:.3f}",
        "-i",
        str(source_audio_path),
        "-ac",
        "1",
        "-ar",
        str(config.tts.sample_rate),
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    run_command(cmd)
    return output_path


def _extract_vocals_stem(
    source_audio_path: Path,
    workspace_dir: Path,
    config: AppConfig,
) -> Path:
    if not module_available("demucs"):
        raise RuntimeError("demucs is not installed")
    if not module_available("torchcodec"):
        raise RuntimeError("torchcodec is not installed")

    cached_vocals_path = workspace_dir / "clone_reference.vocals.wav"
    if cached_vocals_path.exists():
        return cached_vocals_path

    with tempfile.TemporaryDirectory(prefix="shorts-clone-demucs-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
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
            str(source_audio_path),
        ]
        run_command(cmd)

        candidate = output_root / config.compose.source_separation_model / source_audio_path.stem / "vocals.wav"
        if not candidate.exists():
            matches = sorted(output_root.glob(f"**/{source_audio_path.stem}/vocals.wav"))
            if not matches:
                raise RuntimeError("Demucs completed but no `vocals.wav` stem was produced")
            candidate = matches[0]

        ensure_parent(cached_vocals_path)
        shutil.copy2(candidate, cached_vocals_path)

    return cached_vocals_path
