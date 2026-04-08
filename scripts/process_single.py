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
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    from src.pipeline import process_video
    from src.utils import load_config

    config = load_config(args.config)
    output_path = process_video(args.video, config)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

