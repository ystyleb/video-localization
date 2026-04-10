from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process a single short video into an English dub.")
    parser.add_argument("video", type=Path, help="Path to the input video file")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional path to config.yaml",
    )
    parser.add_argument(
        "--tts-provider",
        choices=["vibevoice_realtime", "voxcpm2", "kokoro", "macos_say"],
        default=None,
        help="Optional TTS provider override for this single run",
    )
    parser.add_argument(
        "--voice-clone",
        action="store_true",
        help="Enable clone mode. If the current provider is VibeVoice-Realtime, it will switch to voxcpm2.",
    )
    parser.add_argument(
        "--reference-wav",
        type=Path,
        default=None,
        help="Optional manual reference audio for clone mode. If omitted, a reference clip is auto-extracted from the source video.",
    )
    parser.add_argument(
        "--reference-text",
        default=None,
        help="Optional transcript for --reference-wav. Recommended when using a manual prompt clip.",
    )
    parser.add_argument(
        "--voxcpm2-base-url",
        default=None,
        help="Optional base URL override for the built-in VoxCPM HTTP runner.",
    )
    parser.add_argument(
        "--disable-auto-reference",
        action="store_true",
        help="Disable automatic reference extraction from the source video during clone mode.",
    )
    parser.add_argument(
        "--line-sync",
        action="store_true",
        help="Force one subtitle line per TTS chunk for tighter sentence timing.",
    )
    parser.add_argument(
        "--target-language",
        default=None,
        help="Target language code (e.g. ja, fr, es, ko). Defaults to 'en' (English).",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    from src.pipeline import process_video
    from src.tts import apply_line_sync_tts_defaults
    from src.utils import load_config

    config = load_config(args.config)
    if args.tts_provider:
        config.tts.provider = args.tts_provider
        config.tts.fallback_provider = None

    if args.voice_clone:
        config.tts.voice_mode = "clone"
        if config.tts.provider == "vibevoice_realtime":
            config.tts.provider = "voxcpm2"
            config.tts.fallback_provider = None

    if args.reference_wav:
        config.tts.reference_wav = str(args.reference_wav)
    if args.reference_text:
        config.tts.reference_text = args.reference_text
    if args.voxcpm2_base_url:
        config.tts.voxcpm2_base_url = args.voxcpm2_base_url
    if args.disable_auto_reference:
        config.tts.auto_reference_from_source = False
    if args.line_sync:
        apply_line_sync_tts_defaults(config)

    if args.target_language:
        from src.translate import LANGUAGE_NAMES

        config.translate.target_language = args.target_language
        config.translate.target_language_name = LANGUAGE_NAMES.get(
            args.target_language,
            f"natural spoken {args.target_language}",
        )

    output_path = process_video(args.video, config)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
