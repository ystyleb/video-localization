# Shorts Workflow

这份文档记录当前仓库里已经跑通、可复用的中文短视频转英文版流程。

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
- Translate: `Claude Code`
- TTS: `VibeVoice-Realtime-0.5B`
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
- `demucs`
- `ffmpeg`
- `claude`

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

## 当前推荐配置

当前建议直接以仓库里的 [config.yaml](/Users/winson/Workspace/projects/shorts/config.yaml) 为准。

几个最关键的字段是：

```yaml
translate:
  provider: claude_code
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
- 字幕字号缩小到 `14`
- 英文断句和配音句子比之前自然很多
- 改配置后可自动识别并只重跑必要步骤

如果后面继续迭代，建议优先更新这份文件，而不是只把变化散落在对话里。
