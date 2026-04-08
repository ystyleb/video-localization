from __future__ import annotations

import re
from pathlib import Path

from .models import SubtitleSegment
from .utils import ensure_parent

TIMESTAMP_SEPARATOR = " --> "


def parse_srt_timestamp(value: str) -> int:
    hours_text, minutes_text, rest = value.split(":")
    seconds_text, millis_text = rest.split(",")
    hours = int(hours_text)
    minutes = int(minutes_text)
    seconds = int(seconds_text)
    millis = int(millis_text)
    return (((hours * 60) + minutes) * 60 + seconds) * 1000 + millis


def format_srt_timestamp(value_ms: int) -> str:
    hours, remainder = divmod(max(value_ms, 0), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def parse_srt(srt_path: Path) -> list[SubtitleSegment]:
    payload = srt_path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    if not payload:
        return []

    blocks = re.split(r"\n\s*\n", payload)
    segments: list[SubtitleSegment] = []

    for fallback_index, block in enumerate(blocks, start=1):
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue

        try:
            index = int(lines[0])
            timing_line = lines[1]
            text_lines = lines[2:]
        except ValueError:
            index = fallback_index
            timing_line = lines[0]
            text_lines = lines[1:]

        start_text, end_text = timing_line.split(TIMESTAMP_SEPARATOR)
        segments.append(
            SubtitleSegment(
                index=index,
                start_ms=parse_srt_timestamp(start_text.strip()),
                end_ms=parse_srt_timestamp(end_text.strip()),
                text="\n".join(text_lines).strip(),
            )
        )

    return segments


def write_srt(segments: list[SubtitleSegment], output_path: Path) -> Path:
    ensure_parent(output_path)
    lines: list[str] = []
    for ordinal, segment in enumerate(segments, start=1):
        lines.append(str(segment.index or ordinal))
        lines.append(
            f"{format_srt_timestamp(segment.start_ms)}{TIMESTAMP_SEPARATOR}"
            f"{format_srt_timestamp(segment.end_ms)}"
        )
        lines.append(segment.text.strip())
        lines.append("")
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return output_path


def srt_to_styled_ass(srt_path: Path, ass_path: Path, style_config: object) -> dict[str, object]:
    import pysubs2

    subs = pysubs2.load(str(srt_path), encoding="utf-8")

    default_style = pysubs2.SSAStyle(
        fontname=style_config.font_name,
        fontsize=style_config.font_size,
        alignment=style_config.alignment,
        marginv=style_config.margin_v,
        outline=style_config.outline,
        shadow=style_config.shadow,
        primarycolor=_parse_ass_color(style_config.primary_color, pysubs2),
        outlinecolor=_parse_ass_color(style_config.outline_color, pysubs2),
    )
    subs.styles["Default"] = default_style

    ensure_parent(ass_path)
    subs.save(str(ass_path), format_="ass")
    return {
        "output_path": str(ass_path),
        "provider": "pysubs2",
        "metadata": {"segment_count": len(subs.events)},
        "outputs": {"ass_path": str(ass_path)},
        "inputs": {"srt_path": str(srt_path)},
    }


def _parse_ass_color(value: str, pysubs2_module: object) -> object:
    normalized = value.removeprefix("&H").zfill(8)
    alpha = int(normalized[0:2], 16)
    blue = int(normalized[2:4], 16)
    green = int(normalized[4:6], 16)
    red = int(normalized[6:8], 16)
    return pysubs2_module.Color(red, green, blue, alpha)

