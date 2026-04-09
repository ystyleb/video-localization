# Video Localization

这个仓库现在应该被理解成一个产品仓库，不只是一个临时脚本集合。

产品本体是“视频多语言转化 / 本地化 runtime”，负责真正把输入视频变成目标语言版本。Agent skill 只是它的一部分，用来让 Codex、Claude Code、OpenClaw、opencode 之类的 agent 更容易安装、调用、调试这个产品。

## 产品结构

当前仓库同时包含两层能力：

- 运行时层：真正执行 `ASR -> 翻译 -> TTS -> 字幕 -> 合成`
- Agent 集成层：放在 [skills/video-localization-pipeline](skills/video-localization-pipeline) 的内置 skill

这意味着：

- 只用命令行，也可以直接使用这个产品
- 如果你在 Agent 环境里工作，还可以安装仓库自带的 skill，让 agent 帮你运行和排障

完整实操流程见 [WORKFLOW.md](WORKFLOW.zh-CN.md)，Agent 安装方式见 [AGENT_SKILL.md](AGENT_SKILL.zh-CN.md)。

## 当前能力

- 可恢复的单条 / 批量流水线
- `ASR -> 翻译 -> TTS -> 字幕 -> 合成` 模块拆分
- 本地 `Claude Code` 翻译 provider
- 默认技术路线切到 `Qwen3-ASR + VibeVoice-Realtime-0.5B`
- `VoxCPM` 音色克隆路线
- 面向时间对齐问题的局部调试脚本
- 面向 Agent 的内置 skill 分发目录

## 两种使用方式

### 1. 直接使用产品

这是默认入口，不依赖 Agent。

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

4. 跑一条视频

```bash
uv run python -m scripts.process_single input/sample.mp4
```

### 2. 通过 Agent 使用产品

这是增强入口，不是替代入口。

1. 先把这个产品仓库 clone 到本地
2. 用仓库自带脚本安装 skill：

```bash
./scripts/install_agent_skill.sh --platform codex
```

3. 在这个仓库根目录里让 Agent 调用 `scripts.process_single`、`scripts.process_batch`、`scripts.debug_alignment`

重要：

- skill 本身不能凭空生成视频
- 真正出片的是这个仓库里的 runtime 脚本和模型接入
- Agent 只是帮助你操作这个产品

## 快速开始

### 默认单条视频

```bash
uv run python -m scripts.process_single input/sample.mp4
```

### 使用 SiliconFlow 预设

```bash
export SILICONFLOW_API_KEY=...
uv run python -m scripts.process_single input/sample.mp4 --config config.siliconflow.yaml
```

### 音色克隆

```bash
uv run python -m scripts.process_single input/sample.mp4 --voice-clone
```

### 音色克隆 + 逐句对齐

```bash
uv run python -m scripts.process_single input/sample.mp4 --voice-clone --line-sync
```

### 批量处理

```bash
uv run python -m scripts.process_batch
```

## 仓库里的 Agent Skill

内置 skill 位于：

- [SKILL.md](skills/video-localization-pipeline/SKILL.md)
- [openai.yaml](skills/video-localization-pipeline/agents/openai.yaml)

它的职责是：

- 帮 agent 识别当前仓库是不是兼容 runtime
- 调用现有脚本而不是乱拼临时 shell pipeline
- 读取 `workspace/<video>/manifest.json` 做定位
- 在需要时用 `scripts.debug_alignment` 做局部调试

## 当前 provider 约定

### 翻译

默认使用通用的 OpenAI-compatible API 配置：

- `translate.provider: openai_compatible`
- `translate.api_base_url: https://api.openai.com/v1`
- `translate.api_key_env: OPENAI_API_KEY`
- 默认不再依赖本机 `claude` CLI

这条配置不仅可以指向 OpenAI，也可以指向兼容 `/chat/completions` 的其他服务。

仓库里也内置了一份可直接跑的 SiliconFlow 预设：

- [config.siliconflow.yaml](config.siliconflow.yaml)

用法：

```bash
export SILICONFLOW_API_KEY=...
uv run python -m scripts.process_single input/sample.mp4 --config config.siliconflow.yaml
```

如果你还要同时启用音色克隆：

```bash
export SILICONFLOW_API_KEY=...
uv run python -m scripts.process_single input/sample.mp4 --config config.siliconflow.yaml --voice-clone
```

这里要注意：

- `config.siliconflow.yaml` 只解决翻译 provider 和参数
- 真正切到 `VoxCPM2` 仍然要显式带 `--voice-clone`
- 如果不带 `--voice-clone`，TTS 还是按默认 provider 走

如果你要切回 Anthropic API：

- `translate.provider: claude_api`
- 可在 `.env` 或 shell 中设置：
  - `ANTHROPIC_API_KEY`
  - 或 `ANTHROPIC_AUTH_TOKEN`
  - 可选 `ANTHROPIC_BASE_URL`

如果你要继续使用本机 `claude` CLI，也还支持：

- `translate.provider: claude_code`
- 并确保本机 `claude` 已登录可用

### Qwen3-ASR / VibeVoice-Realtime-0.5B / VoxCPM

这些默认按“本地模型 / 官方脚本”接入，不走 API key。

当前代码支持两种方式：

- 优先：安装官方 Python/runtime，直接在进程内调用
- 备选：在 [config.yaml](config.yaml) 中填入本地命令模板

推荐安装方式：

```bash
uv --native-tls pip install qwen-asr
git clone https://github.com/microsoft/VibeVoice.git
cd VibeVoice
uv --native-tls pip install -e .[streamingtts]
uv --native-tls pip install voxcpm
```

注意：

- `VibeVoice-Realtime-0.5B` 官方文档说明它主要面向英文语音输出，所以很适合作为当前仓库“英文配音”这一环
- 当前默认的 `VibeVoice-Realtime` 更适合“描述音色 / 预置 speaker”模式，不适合直接拿任意参考音频做克隆
- 如果要做“从原视频自动抽参考片段，然后克隆音色”的流程，当前项目会切到 `voxcpm2` provider，并优先调用本机 `voxcpm` CLI
- 官方文档同时说明 `Mac M4 Pro` 在他们测试里可达到实时速度，但整体安装路线仍然是从官方仓库安装 runtime

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

### 音色克隆与逐句对齐

当前音色克隆的推荐入口是：

```bash
uv run python -m scripts.process_single input/sample.mp4 --voice-clone
```

这条路线会：

- 自动从源视频提取参考音色片段
- 生成 `clone_reference.wav` 和 `clone_reference.txt`
- 使用 `voxcpm2` provider 合成英文配音

如果你希望尽量按字幕逐句对齐，再加：

```bash
uv run python -m scripts.process_single input/sample.mp4 --voice-clone --line-sync
```

`--line-sync` 会强制 TTS 走更保守的逐句模式，减少相邻字幕被合并成一个英文大句的概率，但整条视频通常会更慢。

如果你已经有一段更干净的参考音频，也可以手动指定：

```bash
uv run python -m scripts.process_single input/sample.mp4 \
  --voice-clone \
  --reference-wav /absolute/path/to/ref.wav \
  --reference-text "reference transcript"
```

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

## 局部调试时间对齐

当你怀疑“中文每句”和“英文每句”的听感对不上时，不需要每次整片输出。

当前可以直接用 `scripts.debug_alignment` 只看一小段：

```bash
uv run python -m scripts.debug_alignment input/sample.mp4 --tts-chunk 1 --report-only
```

看同一个时间窗里的“当前策略 vs 逐句对齐策略”：

```bash
uv run python -m scripts.debug_alignment input/sample.mp4 --tts-chunk 1 --report-only --line-sync
```

对这一个片段重新合成试听：

```bash
uv run python -m scripts.debug_alignment input/sample.mp4 --tts-chunk 1 --resynthesize --line-sync
```

这个脚本会在 `workspace/<video_name>/debug/` 下生成：

- 文本报告
- 原片短预览
- 配音短预览
- 片段级 `resynth.voiceover.wav`
