from __future__ import annotations

import argparse
import os
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check local prerequisites and configured providers for the shorts pipeline."
    )
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

    from src.utils import command_path, load_config, module_available

    config = load_config(args.config)
    rows: list[tuple[str, str, str]] = []
    failures = 0

    def add_row(label: str, ok: bool, detail: str) -> None:
        nonlocal failures
        rows.append((label, "OK" if ok else "MISS", detail))
        if not ok:
            failures += 1

    add_row("ffmpeg", bool(command_path(config.runtime.ffmpeg_bin)), config.runtime.ffmpeg_bin)
    add_row("ffprobe", bool(command_path(config.runtime.ffprobe_bin)), config.runtime.ffprobe_bin)

    if config.translate.provider == "openai_compatible":
        add_row(
            f"translate.env:{config.translate.api_key_env}",
            bool(os.getenv(config.translate.api_key_env)),
            (
                f"set {config.translate.api_key_env} for {config.translate.api_base_url}"
                if config.translate.api_base_url
                else f"set {config.translate.api_key_env}"
            ),
        )
        add_row(
            "translate.api_base_url",
            bool(config.translate.api_base_url),
            config.translate.api_base_url or "set translate.api_base_url",
        )
    elif config.translate.provider in {"claude_code", "claude"}:
        add_row(
            "translate.claude_code_bin",
            bool(command_path(config.translate.claude_code_bin)),
            config.translate.claude_code_bin,
        )
    elif config.translate.provider == "claude_api":
        has_auth = bool(
            os.getenv(config.translate.anthropic_api_key_env)
            or os.getenv(config.translate.anthropic_auth_token_env)
        )
        add_row(
            "translate.anthropic_auth",
            has_auth,
            (
                f"set {config.translate.anthropic_api_key_env} or "
                f"{config.translate.anthropic_auth_token_env} for claude_api provider"
            ),
        )
        add_row(
            "translate.anthropic_base_url",
            bool(config.translate.anthropic_base_url),
            config.translate.anthropic_base_url or "use Anthropic default endpoint",
        )

    _check_asr_provider(config, add_row, module_available)
    _check_tts_provider(config, add_row, command_path)
    _check_compose_provider(config, add_row, module_available)

    width = max(len(label) for label, _, _ in rows)
    for label, status, detail in rows:
        print(f"{label.ljust(width)}  {status:<4}  {detail}")

    print("")
    print(
        "Configured providers: "
        f"asr={config.asr.provider}, translate={config.translate.provider}, tts={config.tts.provider}"
    )

    if failures:
        print(f"Doctor found {failures} missing prerequisites.")
        return 1

    print("Doctor checks passed.")
    return 0


def _check_asr_provider(config: object, add_row: callable, module_available: callable) -> None:
    if config.asr.provider == "qwen3_asr":
        has_python_runtime = module_available("qwen_asr") and module_available("torch")
        add_row(
            "python:qwen_asr",
            has_python_runtime or bool(config.asr.qwen3_command),
            (
                "official qwen-asr package available"
                if has_python_runtime
                else config.asr.qwen3_command or "install `qwen-asr` or set `asr.qwen3_command`"
            ),
        )
    elif config.asr.provider == "faster_whisper":
        add_row(
            "python:faster_whisper",
            module_available("faster_whisper"),
            "install with `uv add faster-whisper` or `uv sync --extra asr`",
        )

    if config.asr.fallback_provider == "faster_whisper":
        add_row(
            "python:faster_whisper(fallback)",
            module_available("faster_whisper"),
            "needed for ASR fallback",
        )


def _check_tts_provider(config: object, add_row: callable, command_path: callable) -> None:
    if config.tts.provider == "vibevoice_realtime":
        has_python_runtime = False
        try:
            from src.utils import module_available

            has_python_runtime = module_available("vibevoice") and module_available("torch")
        except Exception:  # noqa: BLE001
            has_python_runtime = False
        add_row(
            "python:vibevoice",
            has_python_runtime or bool(config.tts.vibevoice_realtime_command),
            (
                "official runtime available"
                if has_python_runtime
                else config.tts.vibevoice_realtime_command
                or "install VibeVoice runtime or set `tts.vibevoice_realtime_command`"
            ),
        )
        add_row(
            "tts.vibevoice_voice",
            bool(config.tts.vibevoice_voice_prompt_pt or config.tts.vibevoice_repo_dir),
            (
                config.tts.vibevoice_voice_prompt_pt
                or config.tts.vibevoice_repo_dir
                or "set `tts.vibevoice_voice_prompt_pt` or `tts.vibevoice_repo_dir`"
            ),
        )
    elif config.tts.provider == "voxcpm2":
        has_builtin_runner = False
        try:
            from src.utils import project_root

            has_builtin_runner = (project_root() / "scripts" / "voxcpm_http_tts.py").exists()
        except Exception:  # noqa: BLE001
            has_builtin_runner = False
        add_row(
            "tts.voxcpm2_runner",
            bool(config.tts.voxcpm2_command or has_builtin_runner),
            (
                config.tts.voxcpm2_command
                or "built-in scripts/voxcpm_http_tts.py (requires a local VoxCPM-compatible HTTP server)"
            ),
        )
    elif config.tts.provider == "kokoro":
        add_row(
            "tts.kokoro_command",
            bool(config.tts.kokoro_command),
            config.tts.kokoro_command or "set a local shell template in config.yaml",
        )
    elif config.tts.provider == "macos_say":
        add_row("macOS say", bool(command_path("say")), "built-in TTS fallback")

    if config.tts.voice_mode == "clone":
        if config.tts.reference_wav:
            clone_reference_detail = config.tts.reference_wav
        elif config.tts.auto_reference_from_source:
            clone_reference_detail = "auto extract from source audio using zh.srt timing"
        else:
            clone_reference_detail = "set tts.reference_wav or enable tts.auto_reference_from_source"
        add_row(
            "tts.clone_reference",
            bool(config.tts.reference_wav or config.tts.auto_reference_from_source),
            clone_reference_detail,
        )

    if config.tts.fallback_provider == "kokoro":
        add_row(
            "tts.kokoro_command(fallback)",
            bool(config.tts.kokoro_command),
            "needed for TTS fallback",
        )
    elif config.tts.fallback_provider == "vibevoice_realtime":
        add_row(
            "tts.vibevoice_realtime_command(fallback)",
            bool(config.tts.vibevoice_realtime_command),
            "needed for VibeVoice fallback if Python runtime is unavailable",
        )
    elif config.tts.fallback_provider == "macos_say":
        add_row("macOS say(fallback)", bool(command_path("say")), "built-in fallback")


def _check_compose_provider(config: object, add_row: callable, module_available: callable) -> None:
    if config.compose.audio_mode != "dub_plus_bgm":
        return

    if config.compose.enable_source_separation:
        if config.compose.source_separation_provider == "demucs":
            add_row(
                "python:demucs",
                module_available("demucs"),
                "needed for background preservation with source separation",
            )
            add_row(
                "python:torchcodec",
                module_available("torchcodec"),
                "needed by demucs when exporting separated audio",
            )
        else:
            add_row(
                "compose.source_separation_provider",
                False,
                f"unsupported provider: {config.compose.source_separation_provider}",
            )


if __name__ == "__main__":
    raise SystemExit(main())
