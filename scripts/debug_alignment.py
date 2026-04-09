from __future__ import annotations

import argparse
import copy
import json
from dataclasses import dataclass
from pathlib import Path

from src.models import AppConfig, MergedSegment, SubtitleSegment
from src.subtitle import format_srt_timestamp, parse_srt, srt_to_styled_ass, write_srt
from src.tts import (
    _smooth_merged_segments_for_tts,
    apply_line_sync_tts_defaults,
    generate_voiceover,
    merge_segments,
)
from src.utils import ensure_parent, load_config, project_root, resolve_path, run_command, workspace_for_video


@dataclass(slots=True)
class DebugWindow:
    start_ms: int
    end_ms: int
    focus_start_ms: int
    focus_end_ms: int
    focus_en_indices: list[int] | None
    label: str
    reason: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a short alignment debug preview and report from existing workspace artifacts."
    )
    parser.add_argument("video", type=Path, help="Path to the input video file")
    parser.add_argument("--config", type=Path, default=None, help="Optional path to config.yaml")
    parser.add_argument(
        "--segment-start",
        type=int,
        default=None,
        help="1-based subtitle segment index to start from (uses zh.srt by default)",
    )
    parser.add_argument(
        "--segment-end",
        type=int,
        default=None,
        help="1-based subtitle segment index to end at (inclusive)",
    )
    parser.add_argument(
        "--tts-chunk",
        type=int,
        default=None,
        help="1-based merged TTS chunk index to inspect",
    )
    parser.add_argument(
        "--segment-track",
        choices=["zh", "en"],
        default="zh",
        help="Which subtitle track to use when selecting by segment index",
    )
    parser.add_argument(
        "--pad-seconds",
        type=float,
        default=0.6,
        help="Extra context to include before/after the selected window",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to workspace/<video>/debug",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Only generate the text report, skip preview videos",
    )
    parser.add_argument(
        "--with-tts-smoothing",
        action="store_true",
        help="Also run TTS text smoothing when reconstructing merged chunks. Slower because it may call the translation model again.",
    )
    parser.add_argument(
        "--resynthesize",
        action="store_true",
        help="Regenerate TTS only for the selected window, then render a fresh short dubbed preview from that snippet.",
    )
    parser.add_argument(
        "--line-sync",
        action="store_true",
        help="Force one subtitle line per TTS chunk for quick A/B timing checks: disable merge continuation and merged-text smoothing.",
    )
    parser.add_argument(
        "--min-segment-chars",
        type=int,
        default=None,
        help="Temporarily override tts.min_segment_chars for this debug run.",
    )
    parser.add_argument(
        "--merge-gap-ms",
        type=int,
        default=None,
        help="Temporarily override tts.merge_gap_ms for this debug run.",
    )
    parser.add_argument(
        "--sentence-aware-merge",
        dest="sentence_aware_merge",
        action="store_true",
        default=None,
        help="Temporarily enable tts.sentence_aware_merge for this debug run.",
    )
    parser.add_argument(
        "--no-sentence-aware-merge",
        dest="sentence_aware_merge",
        action="store_false",
        help="Temporarily disable tts.sentence_aware_merge for this debug run.",
    )
    parser.add_argument(
        "--sentence-merge-max-duration-ms",
        type=int,
        default=None,
        help="Temporarily override tts.sentence_merge_max_duration_ms for this debug run.",
    )
    parser.add_argument(
        "--sentence-merge-max-chars",
        type=int,
        default=None,
        help="Temporarily override tts.sentence_merge_max_chars for this debug run.",
    )
    parser.add_argument(
        "--smooth-merged-text",
        dest="smooth_merged_text",
        action="store_true",
        default=None,
        help="Temporarily enable tts.smooth_merged_text for this debug run.",
    )
    parser.add_argument(
        "--no-smooth-merged-text",
        dest="smooth_merged_text",
        action="store_false",
        help="Temporarily disable tts.smooth_merged_text for this debug run.",
    )
    parser.add_argument(
        "--max-tempo",
        type=float,
        default=None,
        help="Temporarily override tts.max_tempo for this debug run.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    base_config = load_config(args.config)
    root = project_root()
    video_path = resolve_path(args.video, root)
    workspace_dir = workspace_for_video(video_path, base_config, root)
    baseline_config = _apply_workspace_tts_snapshot(base_config, workspace_dir)
    debug_config = _apply_debug_tts_overrides(baseline_config, args)

    zh_srt = workspace_dir / "zh.srt"
    en_srt = workspace_dir / "en.srt"
    en_ass = workspace_dir / "en.ass"
    voiceover_wav = workspace_dir / "voiceover.wav"
    if not zh_srt.exists() or not en_srt.exists() or not en_ass.exists() or not voiceover_wav.exists():
        raise RuntimeError(
            "Missing workspace artifacts. Run the main pipeline first so zh.srt, en.srt, en.ass, and voiceover.wav exist."
        )

    zh_segments = parse_srt(zh_srt)
    en_segments = parse_srt(en_srt)
    baseline_merged_segments = _build_merged_segments(
        en_segments,
        baseline_config,
        apply_tts_smoothing=args.with_tts_smoothing,
    )
    debug_merged_segments = _build_merged_segments(
        en_segments,
        debug_config,
        apply_tts_smoothing=args.with_tts_smoothing,
    )
    window = _select_window(
        zh_segments=zh_segments,
        en_segments=en_segments,
        merged_segments=baseline_merged_segments,
        segment_track=args.segment_track,
        segment_start=args.segment_start,
        segment_end=args.segment_end,
        tts_chunk=args.tts_chunk,
        pad_ms=max(int(args.pad_seconds * 1000), 0),
    )
    label_suffix = _debug_label_suffix(args)
    if label_suffix:
        window.label = f"{window.label}.{label_suffix}"

    output_dir = args.output_dir or (workspace_dir / "debug")
    output_dir = resolve_path(output_dir, root) if not output_dir.is_absolute() else output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    report_path = output_dir / f"{window.label}.report.txt"
    report_text = _build_report(
        window=window,
        zh_segments=zh_segments,
        en_segments=en_segments,
        baseline_merged_segments=baseline_merged_segments,
        debug_merged_segments=debug_merged_segments,
        baseline_config=baseline_config,
        debug_config=debug_config,
        merged_segments_include_smoothing=args.with_tts_smoothing,
    )
    ensure_parent(report_path)
    report_path.write_text(report_text, encoding="utf-8")

    source_preview_path = output_dir / f"{window.label}.source.mp4"
    dubbed_preview_path = output_dir / f"{window.label}.dub.mp4"

    if not args.report_only:
        _render_source_preview(video_path, source_preview_path, window, baseline_config)
        if args.resynthesize:
            resynth_srt_path = output_dir / f"{window.label}.resynth.en.srt"
            resynth_ass_path = output_dir / f"{window.label}.resynth.en.ass"
            resynth_voiceover_path = output_dir / f"{window.label}.resynth.voiceover.wav"
            resynth_preview_path = output_dir / f"{window.label}.resynth.dub.mp4"
            _render_resynth_preview(
                video_path=video_path,
                window=window,
                en_segments=en_segments,
                config=debug_config,
                workspace_dir=workspace_dir,
                source_audio_path=workspace_dir / "source_audio.wav",
                zh_srt_path=zh_srt,
                debug_srt_path=resynth_srt_path,
                debug_ass_path=resynth_ass_path,
                debug_voiceover_path=resynth_voiceover_path,
                output_path=resynth_preview_path,
            )
        else:
            _render_dub_preview(video_path, voiceover_wav, en_ass, dubbed_preview_path, window, baseline_config)

    print(f"Window:   {window.reason}")
    print(f"Report:   {report_path}")
    if not args.report_only:
        print(f"Source:   {source_preview_path}")
        if args.resynthesize:
            print(f"Dubbed:   {resynth_preview_path}")
            print(f"Snippet:  {resynth_voiceover_path}")
        else:
            print(f"Dubbed:   {dubbed_preview_path}")
    return 0


def _build_merged_segments(
    en_segments: list[SubtitleSegment],
    config: AppConfig,
    *,
    apply_tts_smoothing: bool,
) -> list[MergedSegment]:
    merged = merge_segments(
        en_segments,
        min_segment_chars=config.tts.min_segment_chars,
        merge_gap_ms=config.tts.merge_gap_ms,
        sentence_aware_merge=config.tts.sentence_aware_merge,
        sentence_merge_max_duration_ms=config.tts.sentence_merge_max_duration_ms,
        sentence_merge_max_chars=config.tts.sentence_merge_max_chars,
    )
    if not apply_tts_smoothing:
        return merged
    return _smooth_merged_segments_for_tts(merged, config)


def _select_window(
    *,
    zh_segments: list[SubtitleSegment],
    en_segments: list[SubtitleSegment],
    merged_segments: list[MergedSegment],
    segment_track: str,
    segment_start: int | None,
    segment_end: int | None,
    tts_chunk: int | None,
    pad_ms: int,
) -> DebugWindow:
    if tts_chunk is not None:
        if tts_chunk < 1 or tts_chunk > len(merged_segments):
            raise RuntimeError(f"tts-chunk must be between 1 and {len(merged_segments)}")
        chunk = merged_segments[tts_chunk - 1]
        return DebugWindow(
            start_ms=max(chunk.start_ms - pad_ms, 0),
            end_ms=chunk.end_ms + pad_ms,
            focus_start_ms=chunk.start_ms,
            focus_end_ms=chunk.end_ms,
            focus_en_indices=list(chunk.indices),
            label=f"tts_chunk_{tts_chunk:03d}",
            reason=(
                f"TTS chunk {tts_chunk} | "
                f"{_format_ms(chunk.start_ms)} -> {_format_ms(chunk.end_ms)} | "
                f"indices={chunk.indices}"
            ),
        )

    selected_segments = zh_segments if segment_track == "zh" else en_segments
    if not selected_segments:
        raise RuntimeError(f"No {segment_track}.srt segments found")

    start_index = segment_start or 1
    end_index = segment_end or start_index
    if start_index < 1 or end_index < start_index or end_index > len(selected_segments):
        raise RuntimeError(
            f"segment range must be within 1..{len(selected_segments)} for {segment_track}.srt"
        )

    first = selected_segments[start_index - 1]
    last = selected_segments[end_index - 1]
    return DebugWindow(
        start_ms=max(first.start_ms - pad_ms, 0),
        end_ms=last.end_ms + pad_ms,
        focus_start_ms=first.start_ms,
        focus_end_ms=last.end_ms,
        focus_en_indices=list(range(start_index, end_index + 1)) if segment_track == "en" else None,
        label=f"{segment_track}_{start_index:03d}_{end_index:03d}",
        reason=(
            f"{segment_track}.srt segments {start_index}-{end_index} | "
            f"{_format_ms(first.start_ms)} -> {_format_ms(last.end_ms)}"
        ),
    )


def _build_report(
    *,
    window: DebugWindow,
    zh_segments: list[SubtitleSegment],
    en_segments: list[SubtitleSegment],
    baseline_merged_segments: list[MergedSegment],
    debug_merged_segments: list[MergedSegment],
    baseline_config: AppConfig,
    debug_config: AppConfig,
    merged_segments_include_smoothing: bool,
) -> str:
    lines: list[str] = []
    lines.append(f"Window: {window.reason}")
    lines.append(f"Clip range: {_format_ms(window.start_ms)} -> {_format_ms(window.end_ms)}")
    lines.append(f"Baseline TTS config: {_tts_debug_summary(baseline_config)}")
    if _tts_config_changed(baseline_config, debug_config):
        lines.append(f"Debug override config: {_tts_debug_summary(debug_config)}")
    else:
        lines.append("Debug override config: none")
    if (baseline_config.tts.smooth_merged_text or debug_config.tts.smooth_merged_text) and not merged_segments_include_smoothing:
        lines.append(
            "Merged chunk preview note: smoothing is enabled in TTS, but this report skipped smoothing for speed. Add --with-tts-smoothing for an exact chunk preview."
        )
    lines.append("")
    lines.append("Chinese segments:")
    lines.extend(_render_segment_lines(_segments_in_window(zh_segments, window)))
    lines.append("")
    lines.append("English segments:")
    lines.extend(_render_segment_lines(_segments_in_window(en_segments, window)))
    lines.append("")
    lines.append("Baseline merged TTS chunks:")
    chunk_lines = _render_chunk_lines(_chunks_in_window(baseline_merged_segments, window))
    lines.extend(chunk_lines if chunk_lines else ["  (none)"])
    if _tts_config_changed(baseline_config, debug_config):
        lines.append("")
        lines.append("Debug merged TTS chunks:")
        debug_chunk_lines = _render_chunk_lines(_chunks_in_window(debug_merged_segments, window))
        lines.extend(debug_chunk_lines if debug_chunk_lines else ["  (none)"])
    lines.append("")
    lines.append("Note: if a merged TTS chunk spans multiple subtitle indices, spoken phrase boundaries will not line up one-to-one with subtitle slots.")
    return "\n".join(lines).rstrip() + "\n"


def _segments_in_window(segments: list[SubtitleSegment], window: DebugWindow) -> list[SubtitleSegment]:
    return [
        segment
        for segment in segments
        if segment.end_ms > window.start_ms and segment.start_ms < window.end_ms
    ]


def _focus_en_segments(en_segments: list[SubtitleSegment], window: DebugWindow) -> list[SubtitleSegment]:
    if window.focus_en_indices:
        lookup = {segment.index: segment for segment in en_segments}
        return [lookup[index] for index in window.focus_en_indices if index in lookup]
    return [
        segment
        for segment in en_segments
        if segment.end_ms > window.focus_start_ms and segment.start_ms < window.focus_end_ms
    ]


def _chunks_in_window(chunks: list[MergedSegment], window: DebugWindow) -> list[MergedSegment]:
    return [
        chunk
        for chunk in chunks
        if chunk.end_ms > window.start_ms and chunk.start_ms < window.end_ms
    ]


def _render_segment_lines(segments: list[SubtitleSegment]) -> list[str]:
    if not segments:
        return ["  (none)"]
    lines: list[str] = []
    for segment in segments:
        lines.append(
            "  "
            f"[{segment.index:03d}] {_format_ms(segment.start_ms)} -> {_format_ms(segment.end_ms)} "
            f"({(segment.end_ms - segment.start_ms) / 1000:.2f}s)"
        )
        lines.append(f"      {segment.text.strip()}")
    return lines


def _render_chunk_lines(chunks: list[MergedSegment]) -> list[str]:
    lines: list[str] = []
    for ordinal, chunk in enumerate(chunks, start=1):
        lines.append(
            "  "
            f"[chunk {ordinal:03d}] {_format_ms(chunk.start_ms)} -> {_format_ms(chunk.end_ms)} "
            f"({(chunk.end_ms - chunk.start_ms) / 1000:.2f}s) indices={chunk.indices}"
        )
        lines.append(f"      {chunk.text.strip()}")
    return lines


def _render_source_preview(
    video_path: Path,
    output_path: Path,
    window: DebugWindow,
    config: AppConfig,
) -> None:
    ensure_parent(output_path)
    start_seconds = window.start_ms / 1000
    end_seconds = window.end_ms / 1000
    cmd = [
        config.runtime.ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-ss",
        f"{start_seconds:.3f}",
        "-t",
        f"{(end_seconds - start_seconds):.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(output_path),
    ]
    run_command(cmd)


def _render_dub_preview(
    video_path: Path,
    voiceover_path: Path,
    ass_path: Path,
    output_path: Path,
    window: DebugWindow,
    config: AppConfig,
) -> None:
    ensure_parent(output_path)
    start_seconds = window.start_ms / 1000
    end_seconds = window.end_ms / 1000
    filter_path = _escape_filter_path(ass_path)
    filter_complex = (
        f"[0:v]ass={filter_path},trim=start={start_seconds:.3f}:end={end_seconds:.3f},"
        "setpts=PTS-STARTPTS[v];"
        f"[1:a]atrim=start={start_seconds:.3f}:end={end_seconds:.3f},asetpts=PTS-STARTPTS[dub]"
    )
    cmd = [
        config.runtime.ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(voiceover_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[dub]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(output_path),
    ]
    run_command(cmd)


def _render_resynth_preview(
    *,
    video_path: Path,
    window: DebugWindow,
    en_segments: list[SubtitleSegment],
    config: AppConfig,
    workspace_dir: Path,
    source_audio_path: Path,
    zh_srt_path: Path,
    debug_srt_path: Path,
    debug_ass_path: Path,
    debug_voiceover_path: Path,
    output_path: Path,
) -> None:
    selected_segments = _focus_en_segments(en_segments, window)
    if not selected_segments:
        raise RuntimeError("No English subtitle segments overlap the selected debug window")

    shifted_segments = [
        SubtitleSegment(
            index=ordinal,
            start_ms=max(segment.start_ms - window.start_ms, 0),
            end_ms=min(max(segment.end_ms - window.start_ms, 1), window.end_ms - window.start_ms),
            text=segment.text,
        )
        for ordinal, segment in enumerate(selected_segments, start=1)
    ]
    write_srt(shifted_segments, debug_srt_path)

    debug_config = copy.deepcopy(config)
    _reuse_existing_clone_reference_if_available(debug_config, workspace_dir)
    generate_voiceover(
        debug_srt_path,
        debug_voiceover_path,
        debug_config,
        source_audio=source_audio_path,
        zh_srt=zh_srt_path,
        workspace_dir=workspace_dir,
    )
    srt_to_styled_ass(debug_srt_path, debug_ass_path, debug_config.subtitle)

    ensure_parent(output_path)
    start_seconds = window.start_ms / 1000
    end_seconds = window.end_ms / 1000
    clip_duration_seconds = (window.end_ms - window.start_ms) / 1000
    filter_path = _escape_filter_path(debug_ass_path)
    filter_complex = (
        f"[0:v]trim=start={start_seconds:.3f}:end={end_seconds:.3f},setpts=PTS-STARTPTS,"
        f"ass={filter_path}[v];"
        f"[1:a]apad=pad_dur={clip_duration_seconds:.3f},atrim=duration={clip_duration_seconds:.3f},"
        "asetpts=PTS-STARTPTS[dub]"
    )
    cmd = [
        debug_config.runtime.ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(debug_voiceover_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[dub]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(output_path),
    ]
    run_command(cmd)


def _reuse_existing_clone_reference_if_available(config: AppConfig, workspace_dir: Path) -> None:
    if config.tts.voice_mode != "clone":
        return
    reference_wav = workspace_dir / "clone_reference.wav"
    reference_text = workspace_dir / "clone_reference.txt"
    if reference_wav.exists():
        config.tts.reference_wav = str(reference_wav)
        config.tts.auto_reference_from_source = False
    if reference_text.exists():
        config.tts.reference_text = reference_text.read_text(encoding="utf-8").strip()


def _apply_workspace_tts_snapshot(config: AppConfig, workspace_dir: Path) -> AppConfig:
    manifest_path = workspace_dir / "manifest.json"
    if not manifest_path.exists():
        return copy.deepcopy(config)

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return copy.deepcopy(config)

    tts_entry = None
    steps = manifest.get("steps")
    if isinstance(steps, dict):
        tts_entry = steps.get("tts")
    if tts_entry is None:
        tts_entry = manifest.get("tts")
    if not isinstance(tts_entry, dict):
        return copy.deepcopy(config)

    metadata = tts_entry.get("metadata")
    if not isinstance(metadata, dict):
        return copy.deepcopy(config)
    snapshot = metadata.get("config_snapshot")
    if not isinstance(snapshot, dict):
        return copy.deepcopy(config)

    snapshot_config = copy.deepcopy(config)
    for key, value in snapshot.items():
        if hasattr(snapshot_config.tts, key):
            setattr(snapshot_config.tts, key, value)
    return snapshot_config


def _apply_debug_tts_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    debug_config = copy.deepcopy(config)

    if args.line_sync:
        apply_line_sync_tts_defaults(debug_config)

    if args.min_segment_chars is not None:
        debug_config.tts.min_segment_chars = args.min_segment_chars
    if args.merge_gap_ms is not None:
        debug_config.tts.merge_gap_ms = args.merge_gap_ms
    if args.sentence_aware_merge is not None:
        debug_config.tts.sentence_aware_merge = args.sentence_aware_merge
    if args.sentence_merge_max_duration_ms is not None:
        debug_config.tts.sentence_merge_max_duration_ms = args.sentence_merge_max_duration_ms
    if args.sentence_merge_max_chars is not None:
        debug_config.tts.sentence_merge_max_chars = args.sentence_merge_max_chars
    if args.smooth_merged_text is not None:
        debug_config.tts.smooth_merged_text = args.smooth_merged_text
    if args.max_tempo is not None:
        debug_config.tts.max_tempo = args.max_tempo

    return debug_config


def _tts_debug_summary(config: AppConfig) -> str:
    return (
        f"provider={config.tts.provider}, "
        f"voice_mode={config.tts.voice_mode}, "
        f"min_segment_chars={config.tts.min_segment_chars}, "
        f"merge_gap_ms={config.tts.merge_gap_ms}, "
        f"sentence_aware_merge={config.tts.sentence_aware_merge}, "
        f"sentence_merge_max_duration_ms={config.tts.sentence_merge_max_duration_ms}, "
        f"sentence_merge_max_chars={config.tts.sentence_merge_max_chars}, "
        f"smooth_merged_text={config.tts.smooth_merged_text}, "
        f"max_tempo={config.tts.max_tempo:.2f}"
    )


def _tts_config_changed(baseline_config: AppConfig, debug_config: AppConfig) -> bool:
    keys = (
        "provider",
        "voice_mode",
        "min_segment_chars",
        "merge_gap_ms",
        "sentence_aware_merge",
        "sentence_merge_max_duration_ms",
        "sentence_merge_max_chars",
        "smooth_merged_text",
        "max_tempo",
    )
    return any(
        getattr(baseline_config.tts, key) != getattr(debug_config.tts, key)
        for key in keys
    )


def _debug_label_suffix(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.line_sync:
        parts.append("line_sync")
    if args.min_segment_chars is not None:
        parts.append(f"minchars{args.min_segment_chars}")
    if args.merge_gap_ms is not None:
        parts.append(f"gap{args.merge_gap_ms}")
    if args.sentence_aware_merge is False:
        parts.append("no_sent_merge")
    elif args.sentence_aware_merge is True:
        parts.append("sent_merge")
    if args.sentence_merge_max_duration_ms is not None:
        parts.append(f"maxdur{args.sentence_merge_max_duration_ms}")
    if args.sentence_merge_max_chars is not None:
        parts.append(f"maxchars{args.sentence_merge_max_chars}")
    if args.smooth_merged_text is False:
        parts.append("no_smooth")
    elif args.smooth_merged_text is True:
        parts.append("smooth")
    if args.max_tempo is not None:
        tempo_label = f"{args.max_tempo:.2f}".replace(".", "_")
        parts.append(f"tempo{tempo_label}")
    return ".".join(parts)


def _format_ms(value_ms: int) -> str:
    return format_srt_timestamp(value_ms).replace(",", ".")


def _escape_filter_path(path: Path) -> str:
    value = path.as_posix()
    return (
        value.replace("\\", "\\\\")
        .replace(":", r"\:")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


if __name__ == "__main__":
    raise SystemExit(main())
