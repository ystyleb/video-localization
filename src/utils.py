from __future__ import annotations

import importlib.util
import json
import logging
import os
import shlex
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .models import AppConfig, JobManifest, JobState

LOGGER = logging.getLogger("shorts")


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def setup_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def load_config(config_path: Path | None = None) -> AppConfig:
    try:
        from dotenv import load_dotenv
    except ImportError as exc:  # pragma: no cover - depends on environment bootstrap
        raise RuntimeError(
            "python-dotenv is not installed. Run `uv sync` to install project dependencies."
        ) from exc

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on environment bootstrap
        raise RuntimeError("PyYAML is not installed. Run `uv sync` to install project dependencies.") from exc

    root = project_root()
    load_dotenv(root / ".env")
    file_path = config_path or root / "config.yaml"
    data: dict[str, Any] = {}
    if file_path.exists():
        data = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    config = AppConfig.from_dict(data)
    ensure_runtime_dirs(config, root)
    return config


def ensure_runtime_dirs(config: AppConfig, root: Path | None = None) -> None:
    base = root or project_root()
    for relative_path in (
        config.paths.input_dir,
        config.paths.output_dir,
        config.paths.workspace_dir,
    ):
        (base / relative_path).mkdir(parents=True, exist_ok=True)


def resolve_path(path_value: str | Path, root: Path | None = None) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (root or project_root()) / path


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def safe_slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    return cleaned.strip("_") or "job"


def workspace_for_video(video_path: Path, config: AppConfig, root: Path | None = None) -> Path:
    workspace_root = resolve_path(config.paths.workspace_dir, root)
    return workspace_root / safe_slug(video_path.stem)


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return default.copy() if default else {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_status(path: Path) -> JobState:
    return JobState.from_dict(read_json(path))


def save_status(path: Path, state: JobState) -> None:
    write_json(path, state.to_dict())


def init_manifest(path: Path, video_path: Path, config: AppConfig) -> JobManifest:
    existing = JobManifest.from_dict(read_json(path))
    if existing:
        return existing
    timestamp = utcnow_iso()
    manifest = JobManifest(
        video_name=video_path.stem,
        source_video=str(video_path),
        created_at=timestamp,
        updated_at=timestamp,
        version=__version__,
        metadata={"config": config.to_dict()},
    )
    save_manifest(path, manifest)
    return manifest


def save_manifest(path: Path, manifest: JobManifest) -> None:
    manifest.updated_at = utcnow_iso()
    write_json(path, manifest.to_dict())


def should_skip_step(
    step_name: str,
    state: JobState,
    config: AppConfig,
    required_outputs: Iterable[Path],
    upstream_changed: bool,
) -> bool:
    if config.runtime.overwrite or upstream_changed:
        return False
    if not config.runtime.resume:
        return False
    if state.get(step_name) != "completed":
        return False
    return all(output.exists() for output in required_outputs)


def run_command(
    cmd: list[str] | str,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    LOGGER.debug("Running command: %s", cmd)
    completed = subprocess.run(
        cmd,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        text=True,
        shell=isinstance(cmd, str),
        capture_output=True,
    )
    if check and completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        details = stderr or stdout or f"exit code {completed.returncode}"
        raise RuntimeError(f"Command failed: {details}")
    return completed


def ffprobe_duration(path: Path, config: AppConfig) -> float:
    cmd = [
        config.runtime.ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = run_command(cmd)
    return float((result.stdout or "0").strip())


def shell_quote(value: Any) -> str:
    return shlex.quote(str(value))


def render_shell_template(template: str, variables: dict[str, Any]) -> str:
    quoted_variables = {
        key: shell_quote(value) if value is not None else ""
        for key, value in variables.items()
    }
    return template.format(**quoted_variables)


def find_input_videos(directory: Path) -> list[Path]:
    supported = {".mp4", ".mov", ".m4v", ".mkv", ".avi"}
    return sorted(path for path in directory.iterdir() if path.suffix.lower() in supported)


def command_path(name: str) -> str | None:
    return shutil.which(name)


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None
