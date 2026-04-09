# Agent Skill

This repo includes a built-in agent skill at:

- [skills/video-localization-pipeline/SKILL.md](skills/video-localization-pipeline/SKILL.md)

The skill helps agents operate this runtime reliably. It does not replace the runtime.

## What It Does

- Detects whether the current directory is a compatible video localization runtime
- Calls existing scripts: `scripts.process_single`, `scripts.process_batch`, `scripts.debug_alignment`
- Reads `workspace/<video>/manifest.json` for fact-checking
- Uses local alignment debugging instead of full re-processing when possible

## What It Does Not Do

- Generate videos on its own
- Include model weights
- Include runtime scripts
- Build a localization system from scratch without this repo

## Directory Structure

```text
skills/
└── video-localization-pipeline/
    ├── SKILL.md
    ├── agents/openai.yaml
    ├── assets/
    └── references/
```

## Installation

### Using the Install Script

```bash
./scripts/install_agent_skill.sh --platform codex
```

Supported platforms: `codex`, `claude`, `openclaw`, `opencode`

### Manual Installation

#### Codex

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -s "$PWD/skills/video-localization-pipeline" "${CODEX_HOME:-$HOME/.codex}/skills/video-localization-pipeline"
```

#### Claude Code

```bash
mkdir -p "${CLAUDE_HOME:-$HOME/.claude}/skills"
ln -s "$PWD/skills/video-localization-pipeline" "${CLAUDE_HOME:-$HOME/.claude}/skills/video-localization-pipeline"
```

Project-level install:

```bash
mkdir -p "$PWD/.claude/skills"
ln -s "$PWD/skills/video-localization-pipeline" "$PWD/.claude/skills/video-localization-pipeline"
```

#### OpenClaw

```bash
mkdir -p "${OPENCLAW_HOME:-$HOME/.openclaw}/skills"
ln -s "$PWD/skills/video-localization-pipeline" "${OPENCLAW_HOME:-$HOME/.openclaw}/skills/video-localization-pipeline"
```

#### opencode

```bash
mkdir -p "${OPENCODE_HOME:-$HOME/.config/opencode}/skills"
ln -s "$PWD/skills/video-localization-pipeline" "${OPENCODE_HOME:-$HOME/.config/opencode}/skills/video-localization-pipeline"
```

Then add the skills root to `opencode.json`:

```json
{
  "skills": {
    "paths": [
      "/absolute/path/to/opencode/skills"
    ]
  }
}
```

## Recommended Usage

1. Get the runtime working first
2. Install the built-in skill
3. Let the agent operate from this repo's root directory

Suggested prompt:

```text
Use $video-localization-pipeline to operate this video localization repo: run a localized version of this video, inspect manifest outputs, or debug line-sync timing.
```

---

[中文文档](AGENT_SKILL.zh-CN.md)
