from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .models import AppConfig, SubtitleSegment
from .subtitle import parse_srt, write_srt


def translate_srt(zh_srt: Path, en_srt: Path, config: AppConfig) -> dict[str, Any]:
    segments = parse_srt(zh_srt)
    if not segments:
        raise RuntimeError(f"No subtitle segments found in {zh_srt}")

    translations = _translate_segments(segments, config)
    translations = _smooth_translations(segments, translations, config)
    translations = _enforce_wpm_limit(segments, translations, config)

    output_segments = [
        SubtitleSegment(
            index=segment.index,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            text=translation.strip(),
        )
        for segment, translation in zip(segments, translations, strict=True)
    ]

    write_srt(output_segments, en_srt)
    return {
        "output_path": str(en_srt),
        "provider": config.translate.provider,
        "metadata": {"segment_count": len(output_segments)},
        "outputs": {"en_srt": str(en_srt)},
        "inputs": {"zh_srt": str(zh_srt)},
    }


def _translate_segments(segments: list[SubtitleSegment], config: AppConfig) -> list[str]:
    texts = [segment.text for segment in segments]
    batches: list[list[str]] = []
    for start in range(0, len(texts), config.translate.batch_size):
        batches.append(texts[start : start + config.translate.batch_size])

    translated: list[str] = []
    for batch in batches:
        if config.translate.provider == "claude_api":
            translated.extend(_translate_batch_with_claude_api(batch, config))
        elif config.translate.provider == "claude_code":
            translated.extend(_translate_batch_with_claude_code(batch, config))
        elif config.translate.provider == "claude":
            translated.extend(_translate_batch_with_claude_code(batch, config))
        elif config.translate.provider == "passthrough":
            translated.extend(batch)
        else:
            raise RuntimeError(f"Unsupported translation provider: {config.translate.provider}")
    return translated


def _translate_batch_with_claude_api(batch: list[str], config: AppConfig) -> list[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    prompt = {
        "task": "Translate Simplified Chinese subtitles into natural spoken American English.",
        "rules": [
            "Preserve meaning and tone but prefer concise spoken English.",
            "Do not add timestamps, numbering, commentary, or markdown.",
            "Return valid JSON only.",
            "Return exactly one translated string per input item, in the same order.",
        ],
        "items": [{"index": idx, "text": text} for idx, text in enumerate(batch)],
        "output_schema": {"translations": ["string"]},
    }
    response = client.messages.create(
        model=config.translate.model,
        max_tokens=4096,
        temperature=config.translate.temperature,
        system=(
            "You translate subtitle text for short-form videos. "
            "Output compact JSON and nothing else."
        ),
        messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
    )
    content_text = _response_text(response)
    payload = _extract_json_payload(content_text)
    translations = payload.get("translations", [])
    if len(translations) != len(batch):
        raise RuntimeError(
            f"Claude returned {len(translations)} translations for batch of {len(batch)} items"
        )
    return [str(item).strip() for item in translations]


def _enforce_wpm_limit(
    segments: list[SubtitleSegment],
    translations: list[str],
    config: AppConfig,
) -> list[str]:
    over_limit_indices: list[int] = []
    budgets: dict[int, int] = {}

    for index, (segment, translation) in enumerate(zip(segments, translations, strict=True)):
        budget = _segment_word_budget(segment, config)
        budgets[index] = budget
        if _count_words(translation) > budget:
            over_limit_indices.append(index)

    if not over_limit_indices:
        return translations

    if config.translate.provider == "claude_api":
        replacements = _compress_batch_with_claude(
            [translations[index] for index in over_limit_indices],
            [budgets[index] for index in over_limit_indices],
            config,
        )
    elif config.translate.provider in {"claude_code", "claude"}:
        replacements = _compress_batch_with_claude_code(
            [translations[index] for index in over_limit_indices],
            [budgets[index] for index in over_limit_indices],
            config,
        )
    else:
        replacements = [
            _hard_word_cap(translations[index], budgets[index]) for index in over_limit_indices
        ]

    updated = list(translations)
    for item_index, replacement in zip(over_limit_indices, replacements, strict=True):
        updated[item_index] = replacement
    return updated


def _smooth_translations(
    segments: list[SubtitleSegment],
    translations: list[str],
    config: AppConfig,
) -> list[str]:
    if len(segments) != len(translations):
        raise RuntimeError("Segment and translation counts do not match")
    if not config.translate.contextual_smoothing:
        return translations

    if config.translate.provider == "claude_api":
        smoother = _smooth_batch_with_claude
    elif config.translate.provider in {"claude_code", "claude"}:
        smoother = _smooth_batch_with_claude_code
    else:
        return translations

    smoothed: list[str] = []
    for start in range(0, len(segments), config.translate.batch_size):
        batch_segments = segments[start : start + config.translate.batch_size]
        batch_translations = translations[start : start + config.translate.batch_size]
        if len(batch_segments) == 1:
            smoothed.extend(batch_translations)
            continue

        budgets = [_segment_word_budget(segment, config) for segment in batch_segments]
        try:
            replacements = smoother(batch_segments, batch_translations, budgets, config)
        except Exception:  # noqa: BLE001
            replacements = batch_translations

        smoothed.extend(
            _hard_word_cap(str(text).strip(), budget)
            for text, budget in zip(replacements, budgets, strict=True)
        )

    return smoothed


def _compress_batch_with_claude(
    texts: list[str],
    budgets: list[int],
    config: AppConfig,
) -> list[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return [_hard_word_cap(text, budget) for text, budget in zip(texts, budgets, strict=True)]

    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    prompt = {
        "task": "Shorten subtitle lines so they are easier to read aloud in short-form video dubbing.",
        "rules": [
            "Keep the same meaning.",
            "Stay natural and conversational.",
            "Do not exceed the requested word budget for each line.",
            "Avoid broken words and obviously dangling clause endings.",
            "Return valid JSON only.",
        ],
        "items": [
            {"index": idx, "text": text, "max_words": budget}
            for idx, (text, budget) in enumerate(zip(texts, budgets, strict=True))
        ],
        "output_schema": {"translations": ["string"]},
    }
    response = client.messages.create(
        model=config.translate.model,
        max_tokens=2048,
        temperature=0,
        system="Output compact JSON and nothing else.",
        messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
    )
    payload = _extract_json_payload(_response_text(response))
    items = [str(item).strip() for item in payload.get("translations", [])]
    if len(items) != len(texts):
        return [_hard_word_cap(text, budget) for text, budget in zip(texts, budgets, strict=True)]
    return [_hard_word_cap(text, budget) for text, budget in zip(items, budgets, strict=True)]


def _translate_batch_with_claude_code(batch: list[str], config: AppConfig) -> list[str]:
    prompt = {
        "task": "Translate Simplified Chinese subtitles into natural spoken American English.",
        "rules": [
            "Preserve meaning and tone but prefer concise spoken English.",
            "Do not add timestamps, numbering, commentary, or markdown.",
            "Return valid JSON only.",
            "Return exactly one translated string per input item, in the same order.",
        ],
        "items": [{"index": idx, "text": text} for idx, text in enumerate(batch)],
        "output_schema": {"translations": ["string"]},
    }
    payload = _run_claude_code_prompt(prompt, config)
    translations = payload.get("translations", [])
    if len(translations) != len(batch):
        raise RuntimeError(
            f"Claude Code returned {len(translations)} translations for batch of {len(batch)} items"
        )
    return [str(item).strip() for item in translations]


def _compress_batch_with_claude_code(
    texts: list[str],
    budgets: list[int],
    config: AppConfig,
) -> list[str]:
    prompt = {
        "task": "Shorten subtitle lines so they are easier to read aloud in short-form video dubbing.",
        "rules": [
            "Keep the same meaning.",
            "Stay natural and conversational.",
            "Do not exceed the requested word budget for each line.",
            "Avoid broken words and obviously dangling clause endings.",
            "Return valid JSON only.",
        ],
        "items": [
            {"index": idx, "text": text, "max_words": budget}
            for idx, (text, budget) in enumerate(zip(texts, budgets, strict=True))
        ],
        "output_schema": {"translations": ["string"]},
    }
    payload = _run_claude_code_prompt(prompt, config)
    items = [str(item).strip() for item in payload.get("translations", [])]
    if len(items) != len(texts):
        return [_hard_word_cap(text, budget) for text, budget in zip(texts, budgets, strict=True)]
    return [_hard_word_cap(text, budget) for text, budget in zip(items, budgets, strict=True)]


def _smooth_batch_with_claude(
    segments: list[SubtitleSegment],
    translations: list[str],
    budgets: list[int],
    config: AppConfig,
) -> list[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return translations

    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    prompt = {
        "task": "Rewrite consecutive subtitle slots into natural spoken American English.",
        "rules": [
            "Treat the full item list as one continuous narration.",
            "Preserve names, places, dates, and factual meaning from the Chinese source.",
            "You may redistribute words across neighboring subtitle slots, but return exactly one English string per input item in the same order.",
            "Keep each line concise and easy to read aloud.",
            "Avoid broken words, dangling articles, and obviously unfinished clause endings when possible.",
            "Do not exceed the requested max_words budget for each line.",
            "Return valid JSON only.",
        ],
        "items": [
            {
                "index": idx,
                "source_text": segment.text,
                "current_translation": translation,
                "duration_ms": segment.end_ms - segment.start_ms,
                "max_words": budget,
            }
            for idx, (segment, translation, budget) in enumerate(
                zip(segments, translations, budgets, strict=True)
            )
        ],
        "output_schema": {"translations": ["string"]},
    }
    response = client.messages.create(
        model=config.translate.model,
        max_tokens=4096,
        temperature=0,
        system="You rewrite subtitle timing slots for dubbing. Output compact JSON and nothing else.",
        messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
    )
    payload = _extract_json_payload(_response_text(response))
    items = [str(item).strip() for item in payload.get("translations", [])]
    if len(items) != len(translations):
        return translations
    return items


def _smooth_batch_with_claude_code(
    segments: list[SubtitleSegment],
    translations: list[str],
    budgets: list[int],
    config: AppConfig,
) -> list[str]:
    prompt = {
        "task": "Rewrite consecutive subtitle slots into natural spoken American English.",
        "rules": [
            "Treat the full item list as one continuous narration.",
            "Preserve names, places, dates, and factual meaning from the Chinese source.",
            "You may redistribute words across neighboring subtitle slots, but return exactly one English string per input item in the same order.",
            "Keep each line concise and easy to read aloud.",
            "Avoid broken words, dangling articles, and obviously unfinished clause endings when possible.",
            "Do not exceed the requested max_words budget for each line.",
            "Return valid JSON only.",
        ],
        "items": [
            {
                "index": idx,
                "source_text": segment.text,
                "current_translation": translation,
                "duration_ms": segment.end_ms - segment.start_ms,
                "max_words": budget,
            }
            for idx, (segment, translation, budget) in enumerate(
                zip(segments, translations, budgets, strict=True)
            )
        ],
        "output_schema": {"translations": ["string"]},
    }
    payload = _run_claude_code_prompt(prompt, config)
    items = [str(item).strip() for item in payload.get("translations", [])]
    if len(items) != len(translations):
        return translations
    return items


def smooth_spoken_english_chunks(
    texts: list[str],
    durations_ms: list[int],
    config: AppConfig,
) -> list[str]:
    if not config.translate.contextual_smoothing:
        return texts
    if len(texts) != len(durations_ms):
        raise RuntimeError("Chunk and duration counts do not match")
    if len(texts) <= 1:
        return [text.strip() for text in texts]

    budgets = [
        max(1, math.floor(max(duration_ms / 60_000, 0.01) * config.translate.max_words_per_minute))
        for duration_ms in durations_ms
    ]

    if config.translate.provider == "claude_api":
        rewritten = _smooth_spoken_chunks_with_claude(texts, durations_ms, budgets, config)
    elif config.translate.provider in {"claude_code", "claude"}:
        rewritten = _smooth_spoken_chunks_with_claude_code(texts, durations_ms, budgets, config)
    else:
        rewritten = texts

    if len(rewritten) != len(texts):
        return [text.strip() for text in texts]
    return [
        _hard_word_cap(str(text).strip(), budget)
        for text, budget in zip(rewritten, budgets, strict=True)
    ]


def _smooth_spoken_chunks_with_claude(
    texts: list[str],
    durations_ms: list[int],
    budgets: list[int],
    config: AppConfig,
) -> list[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return texts

    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    prompt = {
        "task": "Rewrite each English dubbing chunk so it sounds natural when spoken aloud.",
        "rules": [
            "Preserve names, places, dates, and factual meaning.",
            "Keep each chunk as one natural spoken sentence or phrase.",
            "Fix awkward joins caused by subtitle splitting.",
            "Do not exceed the requested max_words budget for each chunk.",
            "Return valid JSON only.",
        ],
        "items": [
            {
                "index": idx,
                "text": text,
                "duration_ms": duration_ms,
                "max_words": budget,
            }
            for idx, (text, duration_ms, budget) in enumerate(
                zip(texts, durations_ms, budgets, strict=True)
            )
        ],
        "output_schema": {"translations": ["string"]},
    }
    response = client.messages.create(
        model=config.translate.model,
        max_tokens=4096,
        temperature=0,
        system="You rewrite English dubbing chunks. Output compact JSON and nothing else.",
        messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
    )
    payload = _extract_json_payload(_response_text(response))
    items = [str(item).strip() for item in payload.get("translations", [])]
    if len(items) != len(texts):
        return texts
    return items


def _smooth_spoken_chunks_with_claude_code(
    texts: list[str],
    durations_ms: list[int],
    budgets: list[int],
    config: AppConfig,
) -> list[str]:
    prompt = {
        "task": "Rewrite each English dubbing chunk so it sounds natural when spoken aloud.",
        "rules": [
            "Preserve names, places, dates, and factual meaning.",
            "Keep each chunk as one natural spoken sentence or phrase.",
            "Fix awkward joins caused by subtitle splitting.",
            "Do not exceed the requested max_words budget for each chunk.",
            "Return valid JSON only.",
        ],
        "items": [
            {
                "index": idx,
                "text": text,
                "duration_ms": duration_ms,
                "max_words": budget,
            }
            for idx, (text, duration_ms, budget) in enumerate(
                zip(texts, durations_ms, budgets, strict=True)
            )
        ],
        "output_schema": {"translations": ["string"]},
    }
    payload = _run_claude_code_prompt(prompt, config)
    items = [str(item).strip() for item in payload.get("translations", [])]
    if len(items) != len(texts):
        return texts
    return items


def _run_claude_code_prompt(prompt: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    command_prefix = [
        config.translate.claude_code_bin,
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        config.translate.claude_code_permission_mode,
    ]
    command_prefix.extend(["--tools", config.translate.claude_code_tools])

    attempts: list[list[str]] = []
    if config.translate.model:
        attempts.append([*command_prefix, "--model", config.translate.model])
    attempts.append(command_prefix)

    last_error: str | None = None
    prompt_text = json.dumps(prompt, ensure_ascii=False)
    for cmd in attempts:
        payload, error_text = _invoke_claude_code(cmd, prompt_text)
        if payload is not None:
            return payload
        last_error = error_text

    raise RuntimeError(f"Claude Code invocation failed: {last_error or 'unknown error'}")


def _invoke_claude_code(cmd: list[str], prompt_text: str) -> tuple[dict[str, Any] | None, str | None]:
    result = subprocess.run(
        cmd,
        input=prompt_text,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
        return None, details

    wrapper = json.loads(result.stdout.strip())
    if wrapper.get("is_error"):
        return None, str(wrapper.get("result", "")).strip()

    raw_result = str(wrapper.get("result", "")).strip()
    return _extract_json_payload(raw_result), None


def _response_text(response: object) -> str:
    chunks: list[str] = []
    for block in getattr(response, "content", []):
        text = getattr(block, "text", None)
        if text:
            chunks.append(text)
    return "\n".join(chunks).strip()


def _extract_json_payload(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    match = re.search(r"\{", cleaned)
    if not match:
        raise RuntimeError("Could not find JSON object in model response")
    decoder = json.JSONDecoder()
    payload, _ = decoder.raw_decode(cleaned[match.start() :])
    return payload


def _count_words(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def _segment_word_budget(segment: SubtitleSegment, config: AppConfig) -> int:
    duration_minutes = max((segment.end_ms - segment.start_ms) / 60_000, 0.01)
    return max(1, math.floor(duration_minutes * config.translate.max_words_per_minute))


def _hard_word_cap(text: str, budget: int) -> str:
    words = re.findall(r"\b[\w'-]+\b", text)
    if len(words) <= budget:
        return text.strip()
    return " ".join(words[:budget]).strip()
