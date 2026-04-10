"""Patch a single subtitle segment's TTS without re-running the full pipeline.

Usage:
    uv run python -m scripts.patch_segment input/1.mov \
        --segment 1 \
        --text "Hi everyone, I'm Winson. It's March 2026." \
        --voice-clone \
        --config config.siliconflow.yaml

    # Use --srt to specify which SRT to patch (default: auto-detect en.srt or multilingual.srt)
    uv run python -m scripts.patch_segment input/1.mov \
        --segment 5 \
        --text "New translation here." \
        --srt multilingual.srt \
        --voice-clone
"""

from __future__ import annotations

import argparse
import copy
import logging
import os
import shutil
import tempfile
from pathlib import Path

from src.models import AppConfig
from src.subtitle import parse_srt, write_srt, srt_to_styled_ass
from src.compose import compose_video
from src.tts import (
    _align_audio,
    _prepare_config_for_clone_tts,
    _synthesize_segment,
)
from src.utils import (
    load_config,
    run_command,
    setup_logging,
    workspace_for_video,
)

LOGGER = logging.getLogger("shorts.patch_segment")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Patch a single segment's TTS audio without re-running the full pipeline.",
    )
    parser.add_argument("video", type=Path, help="Source video file")
    parser.add_argument(
        "--segment",
        type=int,
        required=True,
        help="Segment number to patch (1-based, matching SRT index)",
    )
    parser.add_argument(
        "--text",
        type=str,
        required=True,
        help="New text for the segment",
    )
    parser.add_argument(
        "--srt",
        type=str,
        default=None,
        help="SRT filename to patch (e.g. en.srt, multilingual.srt). Auto-detected if omitted.",
    )
    parser.add_argument("--config", type=Path, default=None, help="Config YAML override")
    parser.add_argument("--voice-clone", action="store_true", help="Enable voice cloning")
    return parser


def _detect_srt_file(workspace_dir: Path) -> tuple[Path, Path, str]:
    """Detect which SRT + voiceover + ASS combo to patch.

    Returns (srt_path, voiceover_path, ass_stem).
    """
    # Prefer multilingual if it exists
    multilingual_srt = workspace_dir / "multilingual.srt"
    if multilingual_srt.exists():
        return (
            multilingual_srt,
            workspace_dir / "voiceover_multilingual.wav",
            "multilingual",
        )
    en_srt = workspace_dir / "en.srt"
    if en_srt.exists():
        return (
            en_srt,
            workspace_dir / "voiceover.wav",
            "en",
        )
    raise FileNotFoundError(f"No SRT file found in {workspace_dir}. Run the full pipeline first.")


def main() -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args()

    video_path = args.video.resolve()
    config = load_config(args.config)
    workspace_dir = workspace_for_video(video_path)

    if not workspace_dir.exists():
        LOGGER.error("Workspace %s does not exist. Run the full pipeline first.", workspace_dir)
        return 1

    # Resolve SRT and voiceover paths
    if args.srt:
        srt_path = workspace_dir / args.srt
        stem = srt_path.stem
        if stem == "multilingual":
            voiceover_path = workspace_dir / "voiceover_multilingual.wav"
        else:
            voiceover_path = workspace_dir / "voiceover.wav"
    else:
        srt_path, voiceover_path, stem = _detect_srt_file(workspace_dir)

    if not srt_path.exists():
        LOGGER.error("SRT file not found: %s", srt_path)
        return 1
    if not voiceover_path.exists():
        LOGGER.error("Voiceover not found: %s", voiceover_path)
        return 1

    # Parse SRT and find target segment
    segments = parse_srt(srt_path)
    target_seg = None
    target_idx = None
    for idx, seg in enumerate(segments):
        if seg.index == args.segment:
            target_seg = seg
            target_idx = idx
            break

    if target_seg is None:
        LOGGER.error("Segment #%d not found in %s (available: %s)",
                      args.segment, srt_path,
                      [s.index for s in segments])
        return 1

    LOGGER.info("Patching segment #%d: '%s' → '%s'",
                args.segment, target_seg.text, args.text)
    LOGGER.info("Time range: %d ms → %d ms", target_seg.start_ms, target_seg.end_ms)

    # Prepare TTS config
    effective_config = copy.deepcopy(config)
    if args.voice_clone:
        effective_config.tts.voice_mode = "clone"
        if effective_config.tts.provider == "vibevoice_realtime":
            effective_config.tts.provider = "voxcpm2"
            effective_config.tts.fallback_provider = None

    if effective_config.tts.voice_mode == "clone":
        effective_config, _ = _prepare_config_for_clone_tts(
            effective_config,
            source_audio=workspace_dir / "source_audio.wav",
            zh_srt=workspace_dir / "zh.srt",
            workspace_dir=workspace_dir,
        )

    target_ms = target_seg.end_ms - target_seg.start_ms

    with tempfile.TemporaryDirectory(prefix="patch-seg-") as tmp:
        tmp_dir = Path(tmp)
        raw_path = tmp_dir / "raw.wav"
        aligned_path = tmp_dir / "aligned.wav"

        # Synthesize new segment
        provider_cache: dict = {}
        _synthesize_segment(args.text, raw_path, effective_config, tmp_dir, provider_cache)
        LOGGER.info("TTS synthesis complete")

        # Align to target duration
        _align_audio(raw_path, aligned_path, target_ms, effective_config)
        LOGGER.info("Aligned to %d ms", target_ms)

        # Splice into existing voiceover
        patched_voiceover = tmp_dir / "patched_voiceover.wav"
        start_sec = target_seg.start_ms / 1000
        end_sec = target_seg.end_ms / 1000

        run_command([
            config.runtime.ffmpeg_bin, "-y",
            "-i", str(voiceover_path),
            "-i", str(aligned_path),
            "-filter_complex",
            f"[0]atrim=0:{start_sec},asetpts=PTS-STARTPTS[before];"
            f"[1]asetpts=PTS-STARTPTS[new];"
            f"[0]atrim={end_sec},asetpts=PTS-STARTPTS[after];"
            f"[before][new][after]concat=n=3:v=0:a=1[out]",
            "-map", "[out]",
            "-ac", "1", "-ar", str(config.tts.sample_rate),
            "-c:a", "pcm_s16le",
            str(patched_voiceover),
        ])
        LOGGER.info("Voiceover spliced")

        # Replace voiceover file atomically
        shutil.copy2(str(patched_voiceover), str(voiceover_path))

    # Update SRT text
    segments[target_idx].text = args.text
    write_srt(segments, srt_path)
    LOGGER.info("Updated SRT: %s", srt_path)

    # Also update TTS SRT if it exists (multilingual has separate tts srt)
    tts_srt = workspace_dir / f"{stem}_tts.srt"
    if tts_srt.exists():
        tts_segments = parse_srt(tts_srt)
        for seg in tts_segments:
            if seg.index == args.segment:
                seg.text = args.text
                break
        write_srt(tts_segments, tts_srt)
        LOGGER.info("Updated TTS SRT: %s", tts_srt)

    # Regenerate ASS subtitle
    ass_path = workspace_dir / f"{stem}.ass"
    srt_to_styled_ass(srt_path, ass_path, config.subtitle)
    LOGGER.info("Regenerated subtitles: %s", ass_path)

    # Recompose video
    output_dir = video_path.resolve().parent.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    if stem == "multilingual":
        final_video = output_dir / f"{video_path.stem}.multilingual.mp4"
    else:
        final_video = output_dir / f"{video_path.stem}.mp4"

    LOGGER.info("Composing final video...")
    compose_video(video_path, voiceover_path, ass_path, final_video, config)

    LOGGER.info("Done! Output: %s", final_video)
    print(final_video)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
