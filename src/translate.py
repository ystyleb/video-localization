from __future__ import annotations

import json
import math
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .models import AppConfig, SubtitleSegment
from .subtitle import parse_srt, write_srt

# Per-language speech rate: (units_per_minute, unit_type)
# "words" = split by spaces/regex, "chars" = character count (for CJK/Thai etc.)
DEFAULT_SPEECH_RATES: dict[str, tuple[int, str]] = {
    "en": (140, "words"),
    "ja": (350, "chars"),
    "fr": (160, "words"),
    "ko": (280, "chars"),
    "es": (160, "words"),
    "de": (130, "words"),
    "ru": (130, "words"),
    "ar": (140, "words"),
    "th": (280, "chars"),
    "pt": (150, "words"),
    "zh": (250, "chars"),
}

# Language display names for translation prompts
LANGUAGE_NAMES: dict[str, str] = {
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
    "it": "natural spoken Italian",
    "vi": "natural spoken Vietnamese",
    "id": "natural spoken Indonesian",
    "hi": "natural spoken Hindi",
    "tr": "natural spoken Turkish",
}


def _target_language_name(config: AppConfig) -> str:
    """Return the target language display name from config or built-in lookup."""
    return (
        config.translate.target_language_name
        or LANGUAGE_NAMES.get(config.translate.target_language, f"natural spoken {config.translate.target_language}")
    )


def _speech_rate_for_language(config: AppConfig) -> tuple[int, str]:
    """Return (units_per_minute, unit_type) for the target language."""
    lang = config.translate.target_language
    if lang in DEFAULT_SPEECH_RATES:
        return DEFAULT_SPEECH_RATES[lang]
    return (config.translate.max_words_per_minute, "words")


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
        if config.translate.provider == "openai_compatible":
            translated.extend(_translate_batch_with_openai_compatible(batch, config))
        elif config.translate.provider == "claude_api":
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


def _translate_batch_with_openai_compatible(batch: list[str], config: AppConfig) -> list[str]:
    lang_name = _target_language_name(config)
    prompt = {
        "task": f"Translate Simplified Chinese subtitles into {lang_name}.",
        "rules": [
            f"Preserve meaning and tone but prefer concise spoken {lang_name}.",
            "Do not add timestamps, numbering, commentary, or markdown.",
            "Return valid JSON only.",
            "Return exactly one translated string per input item, in the same order.",
        ],
        "items": [{"index": idx, "text": text} for idx, text in enumerate(batch)],
        "output_schema": {"translations": ["string"]},
    }
    return _run_openai_compatible_translation_batch(batch, prompt, config)


def _translate_batch_with_claude_api(batch: list[str], config: AppConfig) -> list[str]:
    from anthropic import Anthropic

    lang_name = _target_language_name(config)
    client = _build_anthropic_client(config)
    prompt = {
        "task": f"Translate Simplified Chinese subtitles into {lang_name}.",
        "rules": [
            f"Preserve meaning and tone but prefer concise spoken {lang_name}.",
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
    _, unit_type = _speech_rate_for_language(config)
    over_limit_indices: list[int] = []
    budgets: dict[int, int] = {}

    for index, (segment, translation) in enumerate(zip(segments, translations, strict=True)):
        budget, _ = _segment_unit_budget(segment, config)
        budgets[index] = budget
        if _count_units(translation, unit_type) > budget:
            over_limit_indices.append(index)

    if not over_limit_indices:
        return translations

    if config.translate.provider == "openai_compatible":
        replacements = _compress_batch_with_openai_compatible(
            [translations[index] for index in over_limit_indices],
            [budgets[index] for index in over_limit_indices],
            config,
        )
    elif config.translate.provider == "claude_api":
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
            _hard_unit_cap(translations[index], budgets[index], unit_type) for index in over_limit_indices
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

    if config.translate.provider == "openai_compatible":
        smoother = _smooth_batch_with_openai_compatible
    elif config.translate.provider == "claude_api":
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


def _compress_batch_with_openai_compatible(
    texts: list[str],
    budgets: list[int],
    config: AppConfig,
) -> list[str]:
    return [_hard_word_cap(text, budget) for text, budget in zip(texts, budgets, strict=True)]


def _compress_batch_with_claude(
    texts: list[str],
    budgets: list[int],
    config: AppConfig,
) -> list[str]:
    _, unit_type = _speech_rate_for_language(config)
    if not _has_anthropic_credentials(config):
        return [_hard_unit_cap(text, budget, unit_type) for text, budget in zip(texts, budgets, strict=True)]

    from anthropic import Anthropic

    budget_label = "max_chars" if unit_type == "chars" else "max_words"
    budget_rule = f"Do not exceed the requested {budget_label} budget for each line."
    client = _build_anthropic_client(config)
    prompt = {
        "task": "Shorten subtitle lines so they are easier to read aloud in short-form video dubbing.",
        "rules": [
            "Keep the same meaning.",
            "Stay natural and conversational.",
            budget_rule,
            "Avoid broken words and obviously dangling clause endings.",
            "Return valid JSON only.",
        ],
        "items": [
            {"index": idx, "text": text, budget_label: budget}
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
        return [_hard_unit_cap(text, budget, unit_type) for text, budget in zip(texts, budgets, strict=True)]
    return [_hard_unit_cap(text, budget, unit_type) for text, budget in zip(items, budgets, strict=True)]


def _translate_batch_with_claude_code(batch: list[str], config: AppConfig) -> list[str]:
    lang_name = _target_language_name(config)
    prompt = {
        "task": f"Translate Simplified Chinese subtitles into {lang_name}.",
        "rules": [
            f"Preserve meaning and tone but prefer concise spoken {lang_name}.",
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
    _, unit_type = _speech_rate_for_language(config)
    budget_label = "max_chars" if unit_type == "chars" else "max_words"
    budget_rule = f"Do not exceed the requested {budget_label} budget for each line."
    prompt = {
        "task": "Shorten subtitle lines so they are easier to read aloud in short-form video dubbing.",
        "rules": [
            "Keep the same meaning.",
            "Stay natural and conversational.",
            budget_rule,
            "Avoid broken words and obviously dangling clause endings.",
            "Return valid JSON only.",
        ],
        "items": [
            {"index": idx, "text": text, budget_label: budget}
            for idx, (text, budget) in enumerate(zip(texts, budgets, strict=True))
        ],
        "output_schema": {"translations": ["string"]},
    }
    payload = _run_claude_code_prompt(prompt, config)
    items = [str(item).strip() for item in payload.get("translations", [])]
    if len(items) != len(texts):
        return [_hard_unit_cap(text, budget, unit_type) for text, budget in zip(texts, budgets, strict=True)]
    return [_hard_unit_cap(text, budget, unit_type) for text, budget in zip(items, budgets, strict=True)]


def _smooth_batch_with_claude(
    segments: list[SubtitleSegment],
    translations: list[str],
    budgets: list[int],
    config: AppConfig,
) -> list[str]:
    if not _has_anthropic_credentials(config):
        return translations

    from anthropic import Anthropic

    lang_name = _target_language_name(config)
    _, unit_type = _speech_rate_for_language(config)
    budget_label = "max_chars" if unit_type == "chars" else "max_words"
    client = _build_anthropic_client(config)
    prompt = {
        "task": f"Rewrite consecutive subtitle slots into {lang_name}.",
        "rules": [
            "Treat the full item list as one continuous narration.",
            "Preserve names, places, dates, and factual meaning from the Chinese source.",
            f"You may redistribute words across neighboring subtitle slots, but return exactly one {lang_name} string per input item in the same order.",
            "Keep each line concise and easy to read aloud.",
            "Avoid broken words, dangling articles, and obviously unfinished clause endings when possible.",
            f"Do not exceed the requested {budget_label} budget for each line.",
            "Return valid JSON only.",
        ],
        "items": [
            {
                "index": idx,
                "source_text": segment.text,
                "current_translation": translation,
                "duration_ms": segment.end_ms - segment.start_ms,
                budget_label: budget,
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
    lang_name = _target_language_name(config)
    _, unit_type = _speech_rate_for_language(config)
    budget_label = "max_chars" if unit_type == "chars" else "max_words"
    prompt = {
        "task": f"Rewrite consecutive subtitle slots into {lang_name}.",
        "rules": [
            "Treat the full item list as one continuous narration.",
            "Preserve names, places, dates, and factual meaning from the Chinese source.",
            f"You may redistribute words across neighboring subtitle slots, but return exactly one {lang_name} string per input item in the same order.",
            "Keep each line concise and easy to read aloud.",
            "Avoid broken words, dangling articles, and obviously unfinished clause endings when possible.",
            f"Do not exceed the requested {budget_label} budget for each line.",
            "Return valid JSON only.",
        ],
        "items": [
            {
                "index": idx,
                "source_text": segment.text,
                "current_translation": translation,
                "duration_ms": segment.end_ms - segment.start_ms,
                budget_label: budget,
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


def _smooth_batch_with_openai_compatible(
    segments: list[SubtitleSegment],
    translations: list[str],
    budgets: list[int],
    config: AppConfig,
) -> list[str]:
    _, unit_type = _speech_rate_for_language(config)
    budget_label = "max_chars" if unit_type == "chars" else "max_words"
    lang_name = _target_language_name(config)
    prompt = {
        "task": f"Rewrite subtitle translations into {lang_name} so neighboring lines sound natural when spoken continuously.",
        "rules": [
            "Keep each output aligned to the original subtitle slot count.",
            "You may re-balance wording across adjacent lines to reduce awkward splits.",
            "Preserve names, places, numbers, and factual meaning.",
            f"Do not exceed the requested {budget_label} budget for each line.",
            "Return valid JSON only.",
        ],
        "items": [
            {
                "index": idx,
                "source_text": segment.text,
                "draft_translation": translation,
                "duration_ms": segment.end_ms - segment.start_ms,
                budget_label: budget,
            }
            for idx, (segment, translation, budget) in enumerate(
                zip(segments, translations, budgets, strict=True)
            )
        ],
        "output_schema": {"translations": ["string"]},
    }
    payload = _run_openai_compatible_prompt(
        prompt,
        config,
        system="You rewrite subtitle translations for dubbing. Output compact JSON and nothing else.",
        max_tokens=_openai_compatible_rewrite_max_tokens(len(translations)),
        temperature=0,
    )
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

    rate, unit_type = _speech_rate_for_language(config)
    budgets = [
        max(1, math.floor(max(duration_ms / 60_000, 0.01) * rate))
        for duration_ms in durations_ms
    ]

    if config.translate.provider == "openai_compatible":
        rewritten = _smooth_spoken_chunks_with_openai_compatible(texts, durations_ms, budgets, config)
    elif config.translate.provider == "claude_api":
        rewritten = _smooth_spoken_chunks_with_claude(texts, durations_ms, budgets, config)
    elif config.translate.provider in {"claude_code", "claude"}:
        rewritten = _smooth_spoken_chunks_with_claude_code(texts, durations_ms, budgets, config)
    else:
        rewritten = texts

    if len(rewritten) != len(texts):
        return [text.strip() for text in texts]
    return [
        _hard_unit_cap(str(text).strip(), budget, unit_type)
        for text, budget in zip(rewritten, budgets, strict=True)
    ]


def _smooth_spoken_chunks_with_claude(
    texts: list[str],
    durations_ms: list[int],
    budgets: list[int],
    config: AppConfig,
) -> list[str]:
    if not _has_anthropic_credentials(config):
        return texts

    from anthropic import Anthropic

    _, unit_type = _speech_rate_for_language(config)
    budget_label = "max_chars" if unit_type == "chars" else "max_words"
    client = _build_anthropic_client(config)
    prompt = {
        "task": "Rewrite each dubbing chunk so it sounds natural when spoken aloud.",
        "rules": [
            "Preserve names, places, dates, and factual meaning.",
            "Keep each chunk as one natural spoken sentence or phrase.",
            "Fix awkward joins caused by subtitle splitting.",
            f"Do not exceed the requested {budget_label} budget for each chunk.",
            "Return valid JSON only.",
        ],
        "items": [
            {
                "index": idx,
                "text": text,
                "duration_ms": duration_ms,
                budget_label: budget,
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
        system="You rewrite dubbing chunks for video localization. Output compact JSON and nothing else.",
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
    _, unit_type = _speech_rate_for_language(config)
    budget_label = "max_chars" if unit_type == "chars" else "max_words"
    prompt = {
        "task": "Rewrite each dubbing chunk so it sounds natural when spoken aloud.",
        "rules": [
            "Preserve names, places, dates, and factual meaning.",
            "Keep each chunk as one natural spoken sentence or phrase.",
            "Fix awkward joins caused by subtitle splitting.",
            f"Do not exceed the requested {budget_label} budget for each chunk.",
            "Return valid JSON only.",
        ],
        "items": [
            {
                "index": idx,
                "text": text,
                "duration_ms": duration_ms,
                budget_label: budget,
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


def _smooth_spoken_chunks_with_openai_compatible(
    texts: list[str],
    durations_ms: list[int],
    budgets: list[int],
    config: AppConfig,
) -> list[str]:
    _, unit_type = _speech_rate_for_language(config)
    budget_label = "max_chars" if unit_type == "chars" else "max_words"
    prompt = {
        "task": "Rewrite each dubbing chunk so it sounds natural when spoken aloud.",
        "rules": [
            "Preserve names, places, dates, and factual meaning.",
            "Keep each chunk as one natural spoken sentence or phrase.",
            "Fix awkward joins caused by subtitle splitting.",
            f"Do not exceed the requested {budget_label} budget for each chunk.",
            "Return valid JSON only.",
        ],
        "items": [
            {
                "index": idx,
                "text": text,
                "duration_ms": duration_ms,
                budget_label: budget,
            }
            for idx, (text, duration_ms, budget) in enumerate(
                zip(texts, durations_ms, budgets, strict=True)
            )
        ],
        "output_schema": {"translations": ["string"]},
    }
    payload = _run_openai_compatible_prompt(
        prompt,
        config,
        system="You rewrite dubbing chunks for video localization. Output compact JSON and nothing else.",
        max_tokens=_openai_compatible_rewrite_max_tokens(len(texts)),
        temperature=0,
    )
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


def _run_openai_compatible_prompt(
    prompt: dict[str, Any],
    config: AppConfig,
    *,
    system: str,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    api_key = os.getenv(config.translate.api_key_env)
    if not api_key:
        raise RuntimeError(f"{config.translate.api_key_env} is not set")

    base_url = config.translate.api_base_url.rstrip("/")
    last_parse_error: Exception | None = None
    for _ in range(3):
        request_body = {
            "model": config.translate.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=config.translate.request_timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"OpenAI-compatible request failed with HTTP {exc.code}: {error_body or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI-compatible request failed: {exc.reason}") from exc

        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("OpenAI-compatible response did not contain any choices")

        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            text_parts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            content = "".join(text_parts).strip()
        else:
            content = str(content).strip()

        if not content:
            raise RuntimeError("OpenAI-compatible response did not contain message content")

        try:
            return _extract_json_payload(content)
        except (RuntimeError, json.JSONDecodeError) as exc:
            last_parse_error = exc

    raise RuntimeError(
        f"OpenAI-compatible response did not contain valid JSON: {last_parse_error}"
    )


def _run_openai_compatible_translation_batch(
    batch: list[str],
    prompt: dict[str, Any],
    config: AppConfig,
) -> list[str]:
    payload = _run_openai_compatible_prompt(
        prompt,
        config,
        system="You translate subtitle text for short-form videos. Output compact JSON and nothing else.",
        max_tokens=_openai_compatible_translation_max_tokens(len(batch)),
        temperature=config.translate.temperature,
    )
    translations = [str(item).strip() for item in payload.get("translations", [])]
    if len(translations) == len(batch):
        return translations
    if len(batch) == 1:
        raise RuntimeError(
            "OpenAI-compatible provider returned the wrong number of translations for a single-item batch"
        )

    midpoint = max(1, len(batch) // 2)
    left_batch = batch[:midpoint]
    right_batch = batch[midpoint:]
    left_prompt = {
        **prompt,
        "items": [{"index": idx, "text": text} for idx, text in enumerate(left_batch)],
    }
    right_prompt = {
        **prompt,
        "items": [{"index": idx, "text": text} for idx, text in enumerate(right_batch)],
    }
    return _run_openai_compatible_translation_batch(
        left_batch,
        left_prompt,
        config,
    ) + _run_openai_compatible_translation_batch(
        right_batch,
        right_prompt,
        config,
    )


def _openai_compatible_translation_max_tokens(item_count: int) -> int:
    return min(1536, max(512, item_count * 96))


def _openai_compatible_rewrite_max_tokens(item_count: int) -> int:
    return min(2048, max(512, item_count * 96))


def _has_anthropic_credentials(config: AppConfig) -> bool:
    return bool(
        os.getenv(config.translate.anthropic_api_key_env)
        or os.getenv(config.translate.anthropic_auth_token_env)
    )


def _build_anthropic_client(config: AppConfig) -> Any:
    api_key = os.getenv(config.translate.anthropic_api_key_env)
    auth_token = os.getenv(config.translate.anthropic_auth_token_env)
    if not api_key and not auth_token:
        raise RuntimeError(
            f"Neither {config.translate.anthropic_api_key_env} nor "
            f"{config.translate.anthropic_auth_token_env} is set"
        )

    from anthropic import Anthropic

    kwargs: dict[str, Any] = {
        "timeout": float(config.translate.request_timeout_seconds),
    }
    if config.translate.anthropic_base_url:
        kwargs["base_url"] = config.translate.anthropic_base_url
    if api_key:
        kwargs["api_key"] = api_key
    if auth_token:
        kwargs["auth_token"] = auth_token

    return Anthropic(**kwargs)


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


def _count_units(text: str, unit_type: str = "words") -> int:
    """Count text units: words (space-delimited) or chars (for CJK/Thai)."""
    if unit_type == "chars":
        return len(text.replace(" ", ""))
    return len(re.findall(r"\b[\w'-]+\b", text))


def _count_words(text: str) -> int:
    """Count words — delegates to _count_units with the config's target language."""
    return len(re.findall(r"\b[\w'-]+\b", text))


def _segment_unit_budget(segment: SubtitleSegment, config: AppConfig) -> tuple[int, str]:
    """Return (budget, unit_type) for a segment based on target language speech rate."""
    rate, unit_type = _speech_rate_for_language(config)
    duration_minutes = max((segment.end_ms - segment.start_ms) / 60_000, 0.01)
    return max(1, math.floor(duration_minutes * rate)), unit_type


def _segment_word_budget(segment: SubtitleSegment, config: AppConfig) -> int:
    """Legacy word budget — returns just the number for backward compatibility."""
    budget, _ = _segment_unit_budget(segment, config)
    return budget


def _hard_unit_cap(text: str, budget: int, unit_type: str = "words") -> str:
    """Truncate text to fit within budget units."""
    if unit_type == "chars":
        # Count non-space characters but preserve original text structure
        count = 0
        cut_index = len(text)
        for i, ch in enumerate(text):
            if ch != " ":
                count += 1
            if count > budget:
                cut_index = i
                break
        return text[:cut_index].strip()
    words = re.findall(r"\b[\w'-]+\b", text)
    if len(words) <= budget:
        return text.strip()
    return " ".join(words[:budget]).strip()


def _hard_word_cap(text: str, budget: int) -> str:
    """Legacy word cap — for backward compatibility."""
    return _hard_unit_cap(text, budget, "words")
