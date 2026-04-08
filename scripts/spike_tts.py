from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 0.3 spike: run only the TTS stage against an English SRT or sample text."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional path to config.yaml",
    )
    parser.add_argument(
        "--srt",
        type=Path,
        default=None,
        help="Optional English SRT input. If omitted, --text will be used.",
    )
    parser.add_argument(
        "--text",
        default="This is a local TTS spike validation.",
        help="English sample text used when --srt is not provided",
    )
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=4.0,
        help="Target duration when generating a temporary SRT from --text",
    )
    parser.add_argument(
        "--provider",
        choices=["vibevoice_realtime", "voxcpm2", "kokoro", "macos_say"],
        default=None,
        help="Override the TTS provider for this spike run",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    from src.models import SubtitleSegment
    from src.subtitle import write_srt
    from src.tts import generate_voiceover
    from src.utils import ffprobe_duration, load_config, project_root, resolve_path

    config = load_config(args.config)
    if args.provider:
        config.tts.provider = args.provider
        config.tts.fallback_provider = None

    root = project_root()
    workspace = resolve_path(config.paths.workspace_dir, root) / "tts_spike"
    workspace.mkdir(parents=True, exist_ok=True)

    srt_path = args.srt if args.srt else workspace / "tts_spike.en.srt"
    if not args.srt:
        segments = [
            SubtitleSegment(
                index=1,
                start_ms=0,
                end_ms=max(int(args.duration_seconds * 1000), 1000),
                text=args.text.strip(),
            )
        ]
        write_srt(segments, srt_path)

    output_wav = workspace / "tts_spike.voiceover.wav"
    result = generate_voiceover(srt_path, output_wav, config)
    print(f"Provider:  {result['provider']}")
    print(f"Input SRT: {srt_path}")
    print(f"Output:    {output_wav}")
    print(f"Duration:  {ffprobe_duration(output_wav, config):.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
