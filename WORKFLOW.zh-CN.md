# Video Localization Workflow

这份文档记录当前仓库作为“视频多语言转化产品 runtime”时，已经跑通、可复用的工作流。

先明确边界：

- 这个仓库本身是产品执行层，负责真正出片
- 仓库里的 `skills/video-localization-pipeline/` 是 Agent 集成层
- skill 不能脱离这个 runtime 单独生成视频，它只是帮助 agent 调用本仓库已有能力

当前参考实现仍然是“中文短视频转英文版”，但仓库定位已经不再限制在单一语言对

目标是：

- 保留原视频背景音和环境氛围
- 尽量去掉中文原声
- 用英文配音替换中文讲述
- 叠加英文字幕
- 输出可直接检查的英文成片

## 当前流程

当前主流程是：

1. `ASR`
2. `Translate`
3. `TTS`
4. `Subtitle`
5. `Compose`

具体含义：

1. 从原视频提取音频并生成 `zh.srt`
2. 把中文字幕翻译成自然口语英文
3. 生成英文配音 `voiceover.wav`
4. 把英文字幕转成带样式的 `en.ass`
5. 保留背景音、混入英文配音，并烧录英文字幕，输出最终视频

## 当前默认技术路线

- ASR: `Qwen3-ASR`
- Translate: `OpenAI-compatible API`
- TTS: `VibeVoice-Realtime-0.5B`（默认描述音色）/ `VoxCPM-0.5B`（clone 模式）
- Subtitle rendering: `pysubs2` + ASS
- Compose: `ffmpeg`
- Background preservation: `demucs` + `ffmpeg amix`

## 我们当前确认过的成片策略

这套策略就是现在推荐的默认做法：

- `compose.audio_mode: dub_plus_bgm`
- `compose.enable_source_separation: true`
- `compose.source_separation_provider: demucs`
- `compose.source_separation_model: htdemucs`
- `compose.bgm_gain_db: -12`
- `subtitle.font_size: 14`

含义是：

- 先从原视频中提取原始音轨
- 用 `demucs --two-stems=vocals` 分离出 `no_vocals.wav`
- 用英文配音替换中文解说
- 把分离出的背景音以较低音量混回去
- 英文字幕保持小一号，避免压住底部已有中文字幕

## 一次性准备

### 1. 安装项目依赖

```bash
uv sync
brew install ffmpeg
```

### 2. 安装背景音分离依赖

```bash
uv --native-tls pip install demucs
uv --native-tls pip install torchcodec
```

说明：

- `demucs` 用来做人声分离
- `torchcodec` 是当前 `demucs` 导出分离音频时需要的依赖
- 如果你的网络环境证书比较严格，`uv --native-tls` 比普通 `uv pip install` 更稳

### 3. 准备本地模型 / 运行时

推荐至少确认以下项都可用：

- `qwen-asr`
- `vibevoice`
- `voxcpm`
- `demucs`
- `ffmpeg`
- `OPENAI_API_KEY` 或其他兼容 API 的认证方式

检查命令：

```bash
uv run python -m scripts.doctor
```

## 日常运行

### 单条视频

把视频放到 `input/`，然后执行：

```bash
uv run python -m scripts.process_single input/your_video.mp4
```

如果需要自动抽参考音色并做英文克隆配音：

```bash
uv run python -m scripts.process_single input/your_video.mp4 --voice-clone
```

如果这条视频最重要的问题是“每句时间尽量对齐”，用：

```bash
uv run python -m scripts.process_single input/your_video.mp4 --voice-clone --line-sync
```

输出文件会写到：

- `workspace/<video_name>/`
- `output/<video_name>.en.mp4`

### 批量处理

```bash
uv run python -m scripts.process_batch
```

## 产物说明

每条视频都会在 `workspace/<video_name>/` 下生成中间产物：

- `source_audio.wav`: 从原视频抽出的音频
- `zh.srt`: 中文识别字幕
- `en.srt`: 英文字幕
- `en.ass`: 带样式英文字幕
- `voiceover.wav`: 英文配音
- `status.json`: 步骤状态
- `manifest.json`: 输入、输出、配置快照、执行元数据

如果启用了 clone 模式，通常还会看到：

- `clone_reference.wav`: 自动或手动指定的参考音色音频
- `clone_reference.txt`: 参考音色对应文本
- `clone_reference.vocals.wav`: 从源视频分离出来的人声音轨缓存

如果跑过局部对齐调试，还会看到：

- `debug/`: 局部报告、短预览和片段级重合成结果

最终视频输出到：

- `output/<video_name>.en.mp4`

## 断点续跑规则

当前默认 `resume: true`。

现在的续跑逻辑不是只看“文件是否存在”，而是会一起看步骤对应的配置快照。

这意味着：

- 如果你改了 `translate` 配置，会自动重跑翻译以及后续步骤
- 如果你改了 `tts` 配置，会自动重跑配音以及后续步骤
- 如果你改了 `subtitle` 样式，比如字号，会自动重跑字幕和合成
- 如果你改了 `compose` 配置，比如是否保留背景音，会自动重跑合成

所以现在一般不需要手动删 `workspace/` 才能让新配置生效。

## 当前英文断句策略

断句问题现在分两层处理：

### 1. 翻译阶段的上下文重排

翻译不是简单逐条直译。

当前会把一批连续字幕当成一段连续旁白来处理，然后：

- 保留原时间槽数量
- 允许在相邻字幕之间重新分配英文内容
- 避免把地名、人名、年份、短语切成奇怪的半句

### 2. TTS 阶段的句意合并

TTS 不再只按“字少才合并”。

当前还会根据这些信号判断要不要把相邻字幕合成一个配音句子：

- 上一段是不是破词结尾
- 上一段是不是悬空词结尾
- 下一段是不是明显承接上文
- 合并后的时长和长度是否仍在合理范围内

在句意合并之后，还会再对“真正拿去读的英文大句”做一次口语化润色，减少拼接感。

## 当前音色克隆策略

当前项目里，“默认英文配音”和“克隆音色英文配音”是两条不同路线：

- 默认模式：`tts.provider: vibevoice_realtime`
- 克隆模式：`tts.voice_mode: clone`，并切到 `tts.provider: voxcpm2`

clone 模式下，流水线会优先：

- 从 `source_audio.wav` 和 `zh.srt` 中挑一段相对干净的人声
- 写出 `clone_reference.wav`
- 写出对应文本 `clone_reference.txt`
- 用参考音色继续合成整条英文配音

如果已经有更干净的参考音频，也可以手动传：

```bash
uv run python -m scripts.process_single input/your_video.mp4 \
  --voice-clone \
  --reference-wav /absolute/path/to/ref.wav \
  --reference-text "reference transcript"
```

## 当前逐句对齐策略

如果一条视频的主要问题是：

- 中文字幕和英文配音的句子边界对不上
- 英文第 1 句和第 2 句常被合成成一个大句
- 听感上像“上一句中文没说完，英文已经读到下一句”

优先试 `--line-sync`：

```bash
uv run python -m scripts.process_single input/your_video.mp4 --voice-clone --line-sync
```

这个开关会把 TTS 调成更保守的逐句模式：

- `tts.min_segment_chars = 0`
- `tts.merge_gap_ms = 0`
- `tts.sentence_aware_merge = false`
- `tts.smooth_merged_text = false`

效果是尽量“一条字幕对应一条 TTS chunk”。

代价是：

- TTS 调用次数更多
- 整片生成时间通常会明显变长
- 某些视频里听感会更对齐，但整体语流可能比句意合并模式更碎

## 当前推荐配置

当前建议直接以仓库里的 [config.yaml](config.yaml) 为准。

几个最关键的字段是：

```yaml
translate:
  provider: openai_compatible
  api_base_url: https://api.openai.com/v1
  api_key_env: OPENAI_API_KEY
  contextual_smoothing: true

tts:
  provider: vibevoice_realtime
  min_segment_chars: 8
  merge_gap_ms: 250
  sentence_aware_merge: true
  sentence_merge_max_duration_ms: 17000
  sentence_merge_max_chars: 220
  smooth_merged_text: true

subtitle:
  font_size: 14

compose:
  audio_mode: dub_plus_bgm
  enable_source_separation: true
  source_separation_provider: demucs
  source_separation_model: htdemucs
  bgm_gain_db: -12
```

如果你要固定走 clone 路线，可以在单次运行时直接覆盖：

```bash
uv run python -m scripts.process_single input/your_video.mp4 --voice-clone
```

如果你要固定走逐句对齐路线，再加：

```bash
uv run python -m scripts.process_single input/your_video.mp4 --voice-clone --line-sync
```

这两种方式都会把本次运行的 TTS 配置快照写进 `manifest.json`，所以下次能自动识别配置变化并重跑必要步骤。

## 快速调试时间对齐

当问题集中在前几句或某一小段时，不建议每次都整片重跑。

当前推荐用 `scripts.debug_alignment` 只看一个时间窗：

先看当前成片里某个 TTS chunk 的边界：

```bash
uv run python -m scripts.debug_alignment input/your_video.mp4 --tts-chunk 1 --report-only
```

看同一个时间窗里的“原策略 vs 逐句对齐策略”：

```bash
uv run python -m scripts.debug_alignment input/your_video.mp4 --tts-chunk 1 --report-only --line-sync
```

只对这一个片段重新合成试听：

```bash
uv run python -m scripts.debug_alignment input/your_video.mp4 --tts-chunk 1 --resynthesize --line-sync
```

这个脚本会在 `workspace/<video_name>/debug/` 里生成：

- `*.report.txt`: 当前窗口内的中英字幕、TTS chunk 和配置对比
- `*.source.mp4`: 原片短预览
- `*.resynth.dub.mp4`: 片段级英文重合成预览
- `*.resynth.voiceover.wav`: 片段级英文配音

## 遇到问题时先看哪里

### 没有背景音

先检查：

```bash
uv run python -m scripts.doctor
```

重点看：

- `python:demucs`
- `python:torchcodec`

再看对应视频的 `workspace/<video_name>/manifest.json`，确认 `compose.metadata.effective_audio_mode` 是否是：

- `dub_plus_bgm_separated`

如果是 `dub_only`，说明当次成片没有走背景音保留路线。

### 字号没变化

先确认：

- `config.yaml` 里 `subtitle.font_size` 是否正确
- `manifest.json` 里最新 `subtitle.metadata.config_snapshot` 是否已经更新

现在配置变更会自动触发重跑，正常情况下不需要手动删缓存。

### 英文读起来很怪

先看两层：

- `workspace/<video_name>/en.srt`
- `manifest.json` 里 `tts.metadata.merged_segment_count`

判断逻辑：

- 如果 `en.srt` 本身就很碎，优先调翻译重排
- 如果 `en.srt` 看起来还行，但配音很碎，优先调 TTS 合并策略
- 如果 `manifest.json` 里 `tts.metadata.merged_segment_count` 明显比字幕条数少很多，优先试 `--line-sync`
- 如果你只想验证一小段，优先跑 `scripts.debug_alignment`

## 推荐排查顺序

当一条视频效果不对时，按这个顺序看：

1. `uv run python -m scripts.doctor`
2. `workspace/<video_name>/manifest.json`
3. `workspace/<video_name>/zh.srt`
4. `workspace/<video_name>/en.srt`
5. `workspace/<video_name>/voiceover.wav`
6. `output/<video_name>.en.mp4`

## 当前结论

截至现在，这套流程已经覆盖了我们刚确认过的核心要求：

- 背景音保留
- 中文人声替换成英文配音
- clone 模式下可自动抽参考音色并复用
- 时间对齐问题可通过 `--line-sync` 切到逐句模式
- 有一条不需要整片输出的局部调试路径
- 字幕字号缩小到 `14`
- 英文断句和配音句子比之前自然很多
- 改配置后可自动识别并只重跑必要步骤

如果后面继续迭代，建议优先更新这份文件，而不是只把变化散落在对话里。
