from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 0.1 spike: extract audio and burn manual English subtitles onto a sample video."
    )
    parser.add_argument("video", type=Path, help="Path to the source video")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional path to config.yaml",
    )
    parser.add_argument(
        "--text",
        default="English subtitle spike validation.",
        help="Subtitle text to burn into the sample output",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    import src.compose as compose
    from src.asr import extract_audio
    from src.models import SubtitleSegment
    from src.subtitle import srt_to_styled_ass, write_srt
    from src.utils import ffprobe_duration, load_config, project_root, resolve_path, workspace_for_video

    config = load_config(args.config)
    root = project_root()
    video_path = args.video if args.video.is_absolute() else resolve_path(args.video, root)
    workspace = workspace_for_video(video_path, config, root)
    workspace.mkdir(parents=True, exist_ok=True)

    audio_path = workspace / "spike_source_audio.wav"
    srt_path = workspace / "spike_en.srt"
    ass_path = workspace / "spike_en.ass"
    output_path = resolve_path(config.paths.output_dir, root) / f"{video_path.stem}.ffmpeg-spike.mp4"

    extract_audio(video_path, audio_path, config)
    duration_ms = max(int(ffprobe_duration(video_path, config) * 1000), 2000)
    end_ms = max(duration_ms - 300, 1500)
    segments = [
        SubtitleSegment(
            index=1,
            start_ms=300,
            end_ms=end_ms,
            text=args.text.strip(),
        )
    ]
    write_srt(segments, srt_path)
    srt_to_styled_ass(srt_path, ass_path, config.subtitle)
    compose.burn_subtitles(video_path, ass_path, output_path, config)

    print(f"Audio extracted: {audio_path}")
    print(f"ASS subtitle:   {ass_path}")
    print(f"Output video:   {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

