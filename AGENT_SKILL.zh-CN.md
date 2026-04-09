# Agent Skill

这个仓库自带一个 Agent skill：

- [skills/video-localization-pipeline/SKILL.md](skills/video-localization-pipeline/SKILL.md)

它的作用不是替代这个产品，而是帮助 Agent 更可靠地操作这个产品。

## 它负责什么

- 判断当前目录是不是兼容的视频本地化 runtime
- 优先调用仓库现有的 `scripts.process_single`、`scripts.process_batch`、`scripts.debug_alignment`
- 读取 `workspace/<video>/manifest.json` 做事实核对
- 在时间对齐问题上优先使用局部调试，而不是整片重跑

## 它不负责什么

- 不单独提供视频生成能力
- 不包含模型权重
- 不包含完整 runtime 脚本
- 不会在缺少本仓库执行层时凭空搭一个视频本地化系统

## 仓库内目录

```text
skills/
└── video-localization-pipeline/
    ├── SKILL.md
    ├── agents/openai.yaml
    ├── assets/
    └── references/
```

## 安装方式

如果你已经 clone 了这个仓库，优先使用仓库自带安装脚本：

```bash
./scripts/install_agent_skill.sh --platform codex
```

它支持：

- `codex`
- `claude`
- `openclaw`
- `opencode`

如果你想手动安装，也可以直接把这里的 skill 装到对应平台。

### Codex

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -s "$PWD/skills/video-localization-pipeline" "${CODEX_HOME:-$HOME/.codex}/skills/video-localization-pipeline"
```

### Claude Code

```bash
mkdir -p "${CLAUDE_HOME:-$HOME/.claude}/skills"
ln -s "$PWD/skills/video-localization-pipeline" "${CLAUDE_HOME:-$HOME/.claude}/skills/video-localization-pipeline"
```

项目级安装也可以：

```bash
mkdir -p "$PWD/.claude/skills"
ln -s "$PWD/skills/video-localization-pipeline" "$PWD/.claude/skills/video-localization-pipeline"
```

### OpenClaw

```bash
mkdir -p "${OPENCLAW_HOME:-$HOME/.openclaw}/skills"
ln -s "$PWD/skills/video-localization-pipeline" "${OPENCLAW_HOME:-$HOME/.openclaw}/skills/video-localization-pipeline"
```

### opencode

先安装 skill：

```bash
mkdir -p "${OPENCODE_HOME:-$HOME/.config/opencode}/skills"
ln -s "$PWD/skills/video-localization-pipeline" "${OPENCODE_HOME:-$HOME/.config/opencode}/skills/video-localization-pipeline"
```

再确保 `opencode.json` 里包含这个 skills 根目录：

```json
{
  "skills": {
    "paths": [
      "/absolute/path/to/opencode/skills"
    ]
  }
}
```

## 推荐使用方式

1. 先把这个产品仓库跑通
2. 再安装仓库内置 skill
3. 让 Agent 在这个仓库根目录里协助运行和调试

推荐提示词：

```text
Use $video-localization-pipeline to operate this video localization repo: run a localized version of this video, inspect manifest outputs, or debug line-sync timing.
```
