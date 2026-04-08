from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Process all supported videos in the input directory serially."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional path to config.yaml",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Optional override for the input directory",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    from src.pipeline import process_video
    from src.utils import find_input_videos, load_config, project_root, resolve_path, setup_logging

    config = load_config(args.config)
    setup_logging(config.runtime.log_level)

    input_dir = args.input_dir or resolve_path(config.paths.input_dir, project_root())
    videos = find_input_videos(input_dir)
    if not videos:
        print(f"No supported videos found in {input_dir}")
        return 0

    successes: list[tuple[Path, Path]] = []
    failures: list[tuple[Path, str]] = []

    total = len(videos)
    for index, video_path in enumerate(videos, start=1):
        print(f"[{index}/{total}] Processing {video_path.name}")
        try:
            output_path = process_video(video_path, config)
        except Exception as exc:  # noqa: BLE001
            failures.append((video_path, str(exc)))
            print(f"  FAILED: {exc}")
            continue
        successes.append((video_path, output_path))
        print(f"  OK -> {output_path}")

    print("")
    print("Batch summary")
    print(f"  Success: {len(successes)}")
    print(f"  Failed: {len(failures)}")

    if failures:
        print("")
        print("Failures")
        for video_path, error_text in failures:
            print(f"  - {video_path.name}: {error_text}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
