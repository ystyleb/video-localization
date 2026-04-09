#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Install the bundled video-localization-pipeline skill from this product repo.

Usage:
  ./scripts/install_agent_skill.sh [--platform codex|claude|openclaw|opencode] [--copy] [--target-root PATH]

Options:
  --platform     Target platform. Default: codex
  --copy         Copy files instead of creating a symlink
  --target-root  Override the destination skills root directory
  -h, --help     Show this help message
EOF
}

PLATFORM="codex"
MODE="symlink"
TARGET_ROOT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform)
      [[ $# -ge 2 ]] || { echo "Missing value for --platform" >&2; exit 1; }
      PLATFORM="$2"
      shift 2
      ;;
    --copy)
      MODE="copy"
      shift
      ;;
    --target-root)
      [[ $# -ge 2 ]] || { echo "Missing value for --target-root" >&2; exit 1; }
      TARGET_ROOT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_NAME="video-localization-pipeline"
SOURCE_DIR="$REPO_ROOT/skills/$SKILL_NAME"

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Bundled skill not found at $SOURCE_DIR" >&2
  exit 1
fi

case "$PLATFORM" in
  codex)
    DEFAULT_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
    ;;
  claude|claude-code)
    DEFAULT_ROOT="${CLAUDE_HOME:-$HOME/.claude}/skills"
    ;;
  openclaw)
    DEFAULT_ROOT="${OPENCLAW_HOME:-$HOME/.openclaw}/skills"
    ;;
  opencode)
    DEFAULT_ROOT="${OPENCODE_HOME:-$HOME/.config/opencode}/skills"
    ;;
  *)
    echo "Unsupported platform: $PLATFORM" >&2
    usage >&2
    exit 1
    ;;
esac

TARGET_ROOT="${TARGET_ROOT:-$DEFAULT_ROOT}"
TARGET_DIR="$TARGET_ROOT/$SKILL_NAME"

mkdir -p "$TARGET_ROOT"
rm -rf "$TARGET_DIR"

if [[ "$MODE" == "copy" ]]; then
  cp -R "$SOURCE_DIR" "$TARGET_DIR"
  echo "Copied $SKILL_NAME to $TARGET_DIR"
else
  ln -s "$SOURCE_DIR" "$TARGET_DIR"
  echo "Symlinked $SKILL_NAME to $TARGET_DIR"
fi

if [[ "$PLATFORM" == "opencode" ]]; then
  OPENCODE_CONFIG_DIR="${OPENCODE_HOME:-$HOME/.config/opencode}"
  OPENCODE_CONFIG_PATH="$OPENCODE_CONFIG_DIR/opencode.json"
  mkdir -p "$OPENCODE_CONFIG_DIR"

  python3 - "$OPENCODE_CONFIG_PATH" "$TARGET_ROOT" <<'PY'
import json
import pathlib
import sys

config_path = pathlib.Path(sys.argv[1])
skills_root = str(pathlib.Path(sys.argv[2]).expanduser().resolve())

if config_path.exists():
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
else:
    data = {}

if not isinstance(data, dict):
    data = {}

data.setdefault("$schema", "https://opencode.ai/config.json")
skills = data.setdefault("skills", {})
paths = skills.setdefault("paths", [])
if not isinstance(paths, list):
    paths = []
    skills["paths"] = paths

if skills_root not in paths:
    paths.append(skills_root)

config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

  echo "Updated opencode skills paths in $OPENCODE_CONFIG_PATH"
fi
