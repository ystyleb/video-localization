# Shorts Pipeline

本项目用于把中文短视频转成英文 YouTube Shorts 版本，当前已经包含：

- 可恢复的单条 / 批量流水线
- `ASR -> 翻译 -> TTS -> 字幕 -> 合成` 模块拆分
- 本地 `Claude Code` 翻译 provider
- Phase 0 验证脚本

## 快速开始

1. 安装依赖

```bash
uv sync
```

2. 安装系统依赖

```bash
brew install ffmpeg
```

3. 检查当前环境

```bash
uv run python -m scripts.doctor
```

## 当前 provider 约定

### 翻译

默认使用本机 `claude` CLI：

- `translate.provider: claude_code`
- 需要本机 `claude` 已登录可用
- 不强依赖 `ANTHROPIC_API_KEY`

如果你要切回 API：

- `translate.provider: claude_api`
- 并在 `.env` 中设置 `ANTHROPIC_API_KEY`

### Qwen3-ASR / VoxCPM2

这两个默认按“本地命令模板”接入，不走 API key。

你需要在 [config.yaml](/Users/winson/Workspace/projects/shorts/config.yaml) 中填入本地命令模板：

```yaml
asr:
  provider: qwen3_asr
  qwen3_command: "/path/to/your-qwen3-asr-wrapper --input {audio_path} --output {output_srt}"

tts:
  provider: voxcpm2
  voxcpm2_command: "/path/to/your-voxcpm2-wrapper --text-file {text_file} --output {output_path}"
```

模板变量说明：

- `{audio_path}`: 提取好的 WAV 输入
- `{output_srt}`: 需要生成的 SRT 输出
- `{text}` / `{text_file}`: TTS 文本输入
- `{output_path}`: TTS WAV 输出
- `{sample_rate}` / `{voice_description}` / `{reference_wav}`: 可选附加参数

## 验证脚本

FFmpeg 字幕烧录：

```bash
uv run python -m scripts.spike_ffmpeg input/sample.mp4
```

ASR 单独验证：

```bash
uv run python -m scripts.spike_asr input/sample.mp4 --provider qwen3_asr
```

TTS 单独验证：

```bash
uv run python -m scripts.spike_tts --provider macos_say --text "This is a smoke test."
```

## 正式运行

单条视频：

```bash
uv run python -m scripts.process_single input/sample.mp4
```

批量处理：

```bash
uv run python -m scripts.process_batch
```
