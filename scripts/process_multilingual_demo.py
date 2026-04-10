"""Generate a multilingual demo video from a single Chinese source.

Usage:
    uv run python -m scripts.process_multilingual_demo input/1.mov \
        --languages en ja cantonese fr ko sichuan de ru dongbei es ar th hokkien pt \
        --voice-clone \
        --config config.siliconflow.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

from src.asr import transcribe
from src.compose import compose_video
from src.models import AppConfig, SubtitleSegment
from src.pipeline import _run_step
from src.subtitle import parse_srt, srt_to_styled_ass, write_srt
from src.tts import apply_line_sync_tts_defaults, generate_voiceover
from src.utils import (
    init_manifest,
    load_config,
    load_status,
    project_root,
    resolve_path,
    save_manifest,
    save_status,
    setup_logging,
    workspace_for_video,
)

LOGGER = logging.getLogger("shorts.multilingual_demo")

# Language display names for subtitle labels
LANGUAGE_LABELS: dict[str, str] = {
    "en": "English",
    "ja": "日本語",
    "fr": "Français",
    "ko": "한국어",
    "es": "Español",
    "de": "Deutsch",
    "ru": "Русский",
    "ar": "العربية",
    "th": "ภาษาไทย",
    "pt": "Português",
    "cantonese": "粤语",
    "sichuan": "四川话",
    "dongbei": "东北话",
    "hokkien": "闽南话",
}

# Languages that are Chinese dialects — use original Chinese text, no translation
# VoxCPM2 handles dialect pronunciation via voice synthesis
DIALECT_CODES = {"dongbei"}

DIALECT_NAMES: dict[str, str] = {
    "dongbei": "东北话",
}

# Dialects that need text rewriting (not just pass-through)
# VoxCPM2 detects language from text, so Cantonese needs Cantonese-specific characters
DIALECT_REWRITE_CODES = {"cantonese"}

# Per-language speech rate config: (units_per_minute, unit_type)
# unit_type: "words" = split by spaces, "chars" = character count
# These rates represent natural conversational speed for each language.
LANGUAGE_SPEECH_RATE: dict[str, tuple[int, str]] = {
    "en": (140, "words"),       # English: ~140 words/min conversational
    "ja": (350, "chars"),       # Japanese: ~350 chars/min (mora-based)
    "fr": (160, "words"),       # French: slightly faster than English
    "ko": (280, "chars"),       # Korean: ~280 syllable-blocks/min
    "es": (160, "words"),       # Spanish: similar to French
    "de": (130, "words"),       # German: slightly slower (longer words)
    "ru": (130, "words"),       # Russian: similar to German
    "ar": (140, "words"),       # Arabic: similar to English
    "th": (280, "chars"),       # Thai: no spaces, count chars
    "pt": (150, "words"),       # Portuguese: similar to English
    "cantonese": (250, "chars"),# Cantonese: Chinese character based
    "dongbei": (250, "chars"),  # Dongbei: same as standard Chinese
}

# Fallback for unlisted languages
DEFAULT_SPEECH_RATE = (140, "words")


def compute_budget(seg: SubtitleSegment, language: str) -> int:
    """Compute the max units (words or chars) for a segment based on language speech rate."""
    rate, unit_type = LANGUAGE_SPEECH_RATE.get(language, DEFAULT_SPEECH_RATE)
    duration_min = (seg.end_ms - seg.start_ms) / 60_000
    return max(3, int(duration_min * rate))


def count_units(text: str, language: str) -> int:
    """Count text units based on language type (words or chars)."""
    _, unit_type = LANGUAGE_SPEECH_RATE.get(language, DEFAULT_SPEECH_RATE)
    if unit_type == "chars":
        return len(text.replace(" ", ""))
    return len(text.split())


DIALECT_REWRITE_PROMPTS: dict[str, str] = {
    "cantonese": (
        "把以下普通话句子改写成地道的粤语书面表达。"
        "必须使用粤语特征字，如：嘅、咗、嗰、啲、唔、冇、係、噉、嚟、咩、喺、佢。"
        "改写后要让粤语母语者读起来自然。不要用简体中文的表达方式。"
    ),
}

FOREIGN_LANG_NAMES: dict[str, str] = {
    "en": "natural spoken American English",
    "ja": "natural spoken Japanese",
    "fr": "natural spoken French",
    "ko": "natural spoken Korean",
    "es": "natural spoken Spanish",
    "de": "natural spoken German",
    "ru": "natural spoken Russian",
    "ar": "natural spoken Arabic",
    "th": "natural spoken Thai",
    "pt": "natural spoken Brazilian Portuguese",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a multilingual demo video.")
    parser.add_argument("video", type=Path, help="Source video file")
    parser.add_argument(
        "--languages",
        nargs="+",
        required=True,
        help="Target languages in order (e.g. en ja cantonese fr ko)",
    )
    parser.add_argument("--config", type=Path, default=None, help="Config YAML override")
    parser.add_argument("--voice-clone", action="store_true", help="Enable voice cloning")
    parser.add_argument("--line-sync", action="store_true", help="Strict per-line TTS alignment")
    return parser


def regroup_into_sentences(
    segments: list[SubtitleSegment],
    config: AppConfig,
) -> list[SubtitleSegment]:
    """Regroup ASR fragments into complete semantic units.

    Strategy: concatenate all text, ask LLM to insert sentence boundary
    markers (||), then map back to ASR timestamps via character positions.
    """
    import re

    full_text = "".join(seg.text for seg in segments)

    prompt = {
        "task": "在以下连续文本中插入句子边界标记 ||",
        "context": (
            "这段文本来自 ASR 转写，没有标点符号。"
            "请根据语义在每个完整句子结束的地方插入 || 标记。"
        ),
        "rules": [
            "不要修改原文的任何文字，只插入 || 标记。",
            "每个 || 标记表示一个句子的结束。",
            "最后一个句子末尾不需要 ||。",
            "每个句子应该是一个完整的语义单元（一句完整的话）。",
            "不要把太多句子合成一个——一般一个句子 10-30 个字。",
            "返回 JSON 格式。",
        ],
        "text": full_text,
        "output_schema": {"marked_text": "带有||标记的完整文本"},
        "example": {
            "input": "大家好我是温总现在是二零二六年三月时候开始记录今天的视频日记",
            "output": "大家好我是温总现在是二零二六年三月||时候开始记录今天的视频日记",
        },
    }

    api_key = os.getenv(config.translate.api_key_env)
    if not api_key:
        raise RuntimeError(f"{config.translate.api_key_env} is not set")

    base_url = config.translate.api_base_url.rstrip("/")
    request_body = {
        "model": config.translate.model,
        "messages": [
            {"role": "system", "content": "你是中文文本分句工具。只输出 JSON。"},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "max_tokens": 4096,
        "temperature": 0,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))

    content = payload["choices"][0]["message"]["content"]
    cleaned = re.sub(r"^```json\s*", "", content.strip())
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    match_json = re.search(r"\{", cleaned)
    if not match_json:
        raise RuntimeError("No JSON in sentence boundary response")
    decoder = json.JSONDecoder()
    result_json, _ = decoder.raw_decode(cleaned[match_json.start():])
    marked_text = result_json.get("marked_text", "")

    if not marked_text:
        raise RuntimeError("LLM returned empty marked_text")

    # Split by || to get sentences
    sentences = [s.strip() for s in marked_text.split("||") if s.strip()]
    LOGGER.info("LLM split text into %d sentences", len(sentences))

    # Map each sentence back to ASR timestamps via character position
    # Build a character-to-time mapping from original segments
    char_times: list[tuple[int, int]] = []  # (start_ms, end_ms) for each character
    for seg in segments:
        seg_len = len(seg.text)
        if seg_len == 0:
            continue
        for i in range(seg_len):
            frac_start = i / seg_len
            frac_end = (i + 1) / seg_len
            c_start = seg.start_ms + int(frac_start * (seg.end_ms - seg.start_ms))
            c_end = seg.start_ms + int(frac_end * (seg.end_ms - seg.start_ms))
            char_times.append((c_start, c_end))

    regrouped: list[SubtitleSegment] = []
    char_cursor = 0
    for idx, sentence in enumerate(sentences, start=1):
        sent_len = len(sentence)
        if char_cursor + sent_len > len(char_times):
            # Safety: clamp to end
            start_ms = char_times[char_cursor][0] if char_cursor < len(char_times) else segments[-1].start_ms
            end_ms = char_times[-1][1]
        else:
            start_ms = char_times[char_cursor][0]
            end_ms = char_times[char_cursor + sent_len - 1][1]

        regrouped.append(SubtitleSegment(
            index=idx,
            start_ms=start_ms,
            end_ms=end_ms,
            text=sentence,
        ))
        char_cursor += sent_len

    return regrouped


def split_subtitles_by_language(
    segments: list[SubtitleSegment],
    languages: list[str],
) -> list[tuple[str, list[SubtitleSegment]]]:
    """Split subtitle segments evenly across languages."""
    n_langs = len(languages)
    n_segs = len(segments)
    per_lang = max(1, n_segs // n_langs)
    remainder = n_segs % n_langs

    result: list[tuple[str, list[SubtitleSegment]]] = []
    cursor = 0
    for i, lang in enumerate(languages):
        count = per_lang + (1 if i < remainder else 0)
        chunk = segments[cursor : cursor + count]
        if chunk:
            result.append((lang, chunk))
        cursor += count
    return result


def translate_segments_to_language(
    segments: list[SubtitleSegment],
    target_language: str,
    config: AppConfig,
) -> list[SubtitleSegment]:
    """Translate Chinese subtitle segments to a target language via SiliconFlow API."""
    texts = [seg.text for seg in segments]

    # Calculate budget per segment using language-specific speech rate
    budgets = [compute_budget(seg, target_language) for seg in segments]
    _, unit_type = LANGUAGE_SPEECH_RATE.get(target_language, DEFAULT_SPEECH_RATE)
    budget_label = "max_chars" if unit_type == "chars" else "max_words"

    if target_language in DIALECT_CODES:
        # Dialects like dongbei: use original Chinese text as-is
        return [
            SubtitleSegment(
                index=seg.index,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                text=seg.text,
            )
            for seg in segments
        ]
    elif target_language in DIALECT_REWRITE_CODES:
        # Dialects that need text rewriting (e.g., Cantonese needs specific characters)
        rewrite_task = DIALECT_REWRITE_PROMPTS[target_language]
        prompt = {
            "task": rewrite_task,
            "rules": [
                "保留原意，改写后要自然流畅。",
                "每条的字数不要超过原文太多，要能在对应时长内自然说完。",
                "返回 JSON 格式。",
                "返回的条目数量必须与输入一致。",
            ],
            "items": [
                {
                    "index": idx,
                    "text": text,
                    "duration_seconds": round((seg.end_ms - seg.start_ms) / 1000, 1),
                }
                for idx, (text, seg) in enumerate(zip(texts, segments, strict=True))
            ],
            "output_schema": {"translations": ["string"]},
        }
    else:
        lang_name = FOREIGN_LANG_NAMES.get(target_language, f"spoken {target_language}")
        prompt = {
            "task": f"Translate Simplified Chinese subtitles into {lang_name}.",
            "rules": [
                "Preserve meaning and tone but prefer concise spoken language.",
                "Do not add timestamps, numbering, commentary, or markdown.",
                f"IMPORTANT: Each line MUST NOT exceed its {budget_label} budget. Keep translations SHORT and concise.",
                "The translated text must be naturally speakable within the given duration_seconds.",
                "Return valid JSON only.",
                "Return exactly one translated string per input item, in the same order.",
            ],
            "items": [
                {
                    "index": idx,
                    "text": text,
                    budget_label: budget,
                    "duration_seconds": round((seg.end_ms - seg.start_ms) / 1000, 1),
                }
                for idx, (text, budget, seg) in enumerate(
                    zip(texts, budgets, segments, strict=True)
                )
            ],
            "output_schema": {"translations": ["string"]},
        }

    api_key = os.getenv(config.translate.api_key_env)
    if not api_key:
        raise RuntimeError(f"{config.translate.api_key_env} is not set")

    base_url = config.translate.api_base_url.rstrip("/")
    request_body = {
        "model": config.translate.model,
        "messages": [
            {
                "role": "system",
                "content": "You translate/rewrite subtitle text. Output compact JSON and nothing else.",
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "max_tokens": max(512, len(texts) * 96),
        "temperature": 0.1,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Translation API failed with HTTP {exc.code}: {error_body}") from exc

    content = payload["choices"][0]["message"]["content"]
    # Extract JSON from possibly markdown-wrapped response
    import re
    cleaned = re.sub(r"^```json\s*", "", content.strip())
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    match = re.search(r"\{", cleaned)
    if not match:
        raise RuntimeError(f"No JSON found in translation response for {target_language}")
    decoder = json.JSONDecoder()
    result_json, _ = decoder.raw_decode(cleaned[match.start():])
    translations = result_json.get("translations", [])

    if len(translations) != len(segments):
        raise RuntimeError(
            f"Expected {len(segments)} translations for {target_language}, got {len(translations)}"
        )

    return [
        SubtitleSegment(
            index=seg.index,
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            text=str(t).strip(),
        )
        for seg, t in zip(segments, translations, strict=True)
    ]


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config.runtime.log_level)

    root = project_root()
    video_path = args.video if args.video.is_absolute() else resolve_path(args.video, root)
    if not video_path.exists():
        raise FileNotFoundError(f"Input video not found: {video_path}")

    workspace_dir = workspace_for_video(video_path, config, root)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_dir = resolve_path(config.paths.output_dir, root)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_audio = workspace_dir / "source_audio.wav"
    zh_srt = workspace_dir / "zh.srt"

    # --- Step 1: ASR (reuse existing, skip if already done) ---
    if not zh_srt.exists():
        LOGGER.info("Running ASR...")
        transcribe(video_path, source_audio, zh_srt, config)
    else:
        LOGGER.info("Reusing existing zh.srt")

    # --- Step 2: Regroup ASR fragments into complete sentences ---
    all_zh_segments = parse_srt(zh_srt)
    LOGGER.info("Raw ASR segments: %d", len(all_zh_segments))

    sentences_cache = workspace_dir / "sentences.srt"
    if sentences_cache.exists():
        LOGGER.info("Reusing cached sentence regrouping")
        sentence_segments = parse_srt(sentences_cache)
    else:
        LOGGER.info("Regrouping ASR fragments into sentences via LLM...")
        sentence_segments = regroup_into_sentences(all_zh_segments, config)
        write_srt(sentence_segments, sentences_cache)
    LOGGER.info("Regrouped into %d sentences", len(sentence_segments))

    # --- Step 3: Split sentences by language ---
    lang_splits = split_subtitles_by_language(sentence_segments, args.languages)
    for lang, segs in lang_splits:
        label = LANGUAGE_LABELS.get(lang, lang)
        LOGGER.info(
            "  %s (%s): segments %d-%d, %0.1fs-%0.1fs",
            label,
            lang,
            segs[0].index,
            segs[-1].index,
            segs[0].start_ms / 1000,
            segs[-1].end_ms / 1000,
        )

    # --- Step 4: Translate each segment group ---
    multilingual_segments: list[SubtitleSegment] = []
    segment_languages: list[str] = []  # track language per segment
    for lang, zh_segs in lang_splits:
        label = LANGUAGE_LABELS.get(lang, lang)
        cache_path = workspace_dir / f"translated_{lang}.srt"
        if cache_path.exists():
            LOGGER.info("Reusing cached translation for %s", label)
            translated = parse_srt(cache_path)
        else:
            LOGGER.info("Translating %d segments to %s...", len(zh_segs), label)
            translated = translate_segments_to_language(zh_segs, lang, config)
            write_srt(translated, cache_path)
        multilingual_segments.extend(translated)
        segment_languages.extend([lang] * len(translated))

    # Re-index segments
    for i, seg in enumerate(multilingual_segments, start=1):
        seg.index = i

    # Write clean SRT for subtitles (no pace control prefixes)
    multilingual_srt = workspace_dir / "multilingual.srt"
    write_srt(multilingual_segments, multilingual_srt)
    LOGGER.info("Wrote subtitle SRT: %s", multilingual_srt)

    # TTS SRT is the same as subtitle SRT (no pace control prefixes)
    # VoxCPM2's (control) prefix is unreliable for non-Chinese/English languages
    # Speed alignment is handled by the pipeline's _align_audio (ffmpeg atempo)
    tts_srt = workspace_dir / "multilingual_tts.srt"
    write_srt(multilingual_segments, tts_srt)
    LOGGER.info("Wrote TTS SRT: %s", tts_srt)

    # --- Step 4: Generate voiceover with voice cloning ---
    voiceover_wav = workspace_dir / "voiceover_multilingual.wav"

    effective_config = config
    if args.voice_clone:
        effective_config.tts.voice_mode = "clone"
        if effective_config.tts.provider == "vibevoice_realtime":
            effective_config.tts.provider = "voxcpm2"
            effective_config.tts.fallback_provider = None
    if args.line_sync:
        effective_config = apply_line_sync_tts_defaults(effective_config)

    LOGGER.info("Generating multilingual voiceover with VoxCPM2...")
    generate_voiceover(
        tts_srt,
        voiceover_wav,
        effective_config,
        source_audio=source_audio,
        zh_srt=zh_srt,
        workspace_dir=workspace_dir,
    )

    # --- Step 5: Generate styled subtitle file ---
    multilingual_ass = workspace_dir / "multilingual.ass"
    srt_to_styled_ass(multilingual_srt, multilingual_ass, config.subtitle)
    LOGGER.info("Wrote styled subtitles: %s", multilingual_ass)

    # --- Step 6: Compose final video ---
    final_video = output_dir / f"{video_path.stem}.multilingual.mp4"
    LOGGER.info("Composing final video...")
    compose_video(video_path, voiceover_wav, multilingual_ass, final_video, config)

    LOGGER.info("Done! Output: %s", final_video)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
