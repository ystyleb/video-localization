from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 0.2 spike: run only the ASR stage against a sample video."
    )
    parser.add_argument("video", type=Path, help="Path to the source video")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional path to config.yaml",
    )
    parser.add_argument(
        "--provider",
        choices=["qwen3_asr", "faster_whisper"],
        default=None,
        help="Override the ASR provider for this spike run",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    from src.asr import transcribe
    from src.utils import load_config, project_root, resolve_path, workspace_for_video

    config = load_config(args.config)
    if args.provider:
        config.asr.provider = args.provider
        config.asr.fallback_provider = None

    root = project_root()
    video_path = args.video if args.video.is_absolute() else resolve_path(args.video, root)
    workspace = workspace_for_video(video_path, config, root)
    workspace.mkdir(parents=True, exist_ok=True)
    audio_path = workspace / "source_audio.wav"
    output_srt = workspace / "zh.srt"

    result = transcribe(video_path, audio_path, output_srt, config)
    print(f"Provider:  {result['provider']}")
    print(f"Audio:     {audio_path}")
    print(f"Subtitle:  {output_srt}")
    print("")
    print(output_srt.read_text(encoding="utf-8")[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

