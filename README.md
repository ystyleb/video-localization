# Shorts Pipeline

本项目用于把中文短视频转成英文 YouTube Shorts 版本，当前已经包含：

- 可恢复的单条 / 批量流水线
- `ASR -> 翻译 -> TTS -> 字幕 -> 合成` 模块拆分
- 本地 `Claude Code` 翻译 provider
- 默认技术路线切到 `Qwen3-ASR + VibeVoice-Realtime-0.5B`
- Phase 0 验证脚本

完整实操流程见 [WORKFLOW.md](/Users/winson/Workspace/projects/shorts/WORKFLOW.md)。

## 快速开始

1. 安装依赖

```bash
uv sync
```

2. 安装系统依赖

```bash
brew install ffmpeg
```

如果你希望保留原视频背景音，同时尽量去掉中文原声再叠英文配音，还需要安装 source separation：

```bash
uv --native-tls pip install demucs
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

### Qwen3-ASR / VibeVoice-Realtime-0.5B

这两个默认按“本地模型 / 官方脚本”接入，不走 API key。

当前代码支持两种方式：

- 优先：安装官方 Python/runtime，直接在进程内调用
- 备选：在 [config.yaml](/Users/winson/Workspace/projects/shorts/config.yaml) 中填入本地命令模板

推荐安装方式：

```bash
uv --native-tls pip install qwen-asr
git clone https://github.com/microsoft/VibeVoice.git
cd VibeVoice
uv --native-tls pip install -e .[streamingtts]
```

注意：

- `VibeVoice-Realtime-0.5B` 官方文档说明它主要面向英文语音输出，所以很适合作为本项目“英文配音”这一环。
- 官方文档同时说明 `Mac M4 Pro` 在他们测试里可达到实时速度，但整体安装路线仍然是从官方仓库安装 runtime。

示例：

```yaml
asr:
  provider: qwen3_asr
  qwen3_model_id: Qwen/Qwen3-ASR-0.6B
  qwen3_forced_aligner_id: Qwen/Qwen3-ForcedAligner-0.6B
  qwen3_command: null

tts:
  provider: vibevoice_realtime
  vibevoice_model_path: microsoft/VibeVoice-Realtime-0.5B
  vibevoice_repo_dir: /path/to/VibeVoice
  vibevoice_voice_prompt_pt: null
  vibevoice_speaker_name: wayne
  vibevoice_realtime_command: null
```

模板变量说明：

- `{audio_path}`: 提取好的 WAV 输入
- `{output_srt}`: 需要生成的 SRT 输出
- `{text}` / `{text_file}`: TTS 文本输入
- `{output_path}`: TTS WAV 输出
- `{output_dir}` / `{model_path}` / `{speaker_name}` / `{device}` / `{cfg_scale}`: 可选附加参数

推荐的 VibeVoice 官方脚本思路是：

```bash
python demo/realtime_model_inference_from_file.py \
  --model_path microsoft/VibeVoice-Realtime-0.5B \
  --txt_path /tmp/segment.txt \
  --output_dir /tmp/out \
  --speaker_name wayne
```

当前项目内置 provider 也支持直接加载官方 runtime，但前提是你已经把对应依赖装好。

### 合成与背景音

现在支持两种合成模式：

- `compose.audio_mode: dub_only`
  只保留英文配音，不保留原视频背景音
- `compose.audio_mode: dub_plus_bgm`
  保留原视频背景音，并把英文配音混回去

如果要尽量“替换中文人声、保留环境/BGM”，推荐同时开启：

```yaml
compose:
  audio_mode: dub_plus_bgm
  enable_source_separation: true
  source_separation_provider: demucs
  source_separation_model: htdemucs
  source_separation_device: cpu
  bgm_gain_db: -12
```

这条路线会先用 `demucs --two-stems=vocals` 分出 `no_vocals`，再把英文配音和伴奏/环境音重新混合。

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
