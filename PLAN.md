# Shorts: 中文短视频 → YouTube 英文版自动化流水线（执行版 V2）

## Context

目标是将中文短视频批量转化为英文 YouTube 版本，每周稳定处理 30+ 条，尽量减少人工参与。

已知约束：
- 中文字幕已经烧录在原视频中，无法移除
- 英文字幕需要叠加在中文字幕下方或安全区域内
- 运行环境为 Apple Silicon Mac
- 整体方案优先本地自动化，必要时允许单点降级到云 API

当前拟采用技术栈：
- **ASR**：Qwen3-ASR 0.6B，失败时回退到 `faster-whisper`
- **翻译**：Claude API
- **TTS**：VoxCPM2 2B，失败时回退到 `Kokoro` 或远程 TTS API
- **合成**：FFmpeg + ASS
- **语言**：Python 3.11+

---

## MVP 目标

MVP 只解决 1 个核心场景：

1. 输入 1 个中文短视频
2. 自动生成中文转写字幕 `zh.srt`
3. 自动翻译为英文字幕 `en.srt`
4. 自动生成英文配音 `voiceover.wav`
5. 自动叠加英文字幕并输出英文版视频
6. 支持批量串行处理
7. 支持失败后重跑时跳过已完成步骤

MVP 暂不追求：
- 多机并发
- Web UI
- 自动去除原人声
- 完美音色克隆
- 全自动适配所有字幕烧录位置

---

## 实施原则

### 1. 先验证高风险环节，再搭主骨架

最高风险不是 Python 工程结构，而是：
- Qwen3-ASR 在 Apple Silicon 是否可稳定运行
- VoxCPM2 在 Apple Silicon 是否可接受地运行
- 生成音频是否能对齐短视频节奏

因此先做技术可行性验证，再进入完整工程实现。

### 2. SRT 必须结构化处理，不能整份交给 LLM 重写

翻译时只把字幕文本送去模型，编号和时间轴由程序保留和重建，避免：
- 段号丢失
- 时间轴被改写
- 段数不一致
- 输出格式不合法

### 3. 每个视频都要有可恢复状态

批量处理时，任何一步失败都不应导致整个视频从头重做。

每个视频目录下维护：
- `manifest.json`：输入、输出、模型选择、耗时
- `status.json`：各步骤状态，便于断点续跑

### 4. 先追求稳定同步，再追求最佳音质

对于 Shorts，字幕和配音对齐优先级高于极致音色质量。

---

## 项目结构

```text
shorts/
├── pyproject.toml
├── config.yaml
├── .env.example
├── src/
│   ├── __init__.py
│   ├── pipeline.py             # 主流程编排
│   ├── asr.py                  # 音频提取 + 中文转写
│   ├── translate.py            # 结构化翻译字幕文本
│   ├── tts.py                  # 英文配音生成与时长对齐
│   ├── subtitle.py             # SRT/ASS 转换与字幕样式
│   ├── compose.py              # 音视频混流与字幕叠加
│   ├── models.py               # SubtitleSegment / JobState 等数据结构
│   └── utils.py                # 配置、日志、文件、命令执行
├── scripts/
│   ├── process_single.py
│   └── process_batch.py
├── input/
├── output/
└── workspace/
    └── {video_name}/
        ├── source_audio.wav
        ├── zh.srt
        ├── en.srt
        ├── en.ass
        ├── voiceover.wav
        ├── manifest.json
        └── status.json
```

说明：
- `source_audio.wav`：从原视频中抽出的音频，供 ASR 使用
- `manifest.json`：记录参数、模型、输出路径、耗时、版本
- `status.json`：记录 `asr/translate/tts/subtitle/compose` 是否完成

---

## 配置设计

### `config.yaml`

应至少包含以下内容：

```yaml
paths:
  input_dir: input
  output_dir: output
  workspace_dir: workspace

runtime:
  resume: true
  overwrite: false
  log_level: INFO

asr:
  provider: qwen3_asr
  fallback_provider: faster_whisper
  language: zh

translate:
  provider: claude
  model: claude-3-5-sonnet
  batch_size: 30
  max_words_per_minute: 140

tts:
  provider: voxcpm2
  fallback_provider: kokoro
  voice_mode: description
  voice_description: "A young adult, natural, energetic, clear American English voice"
  reference_wav: null
  max_tempo: 1.30
  min_segment_chars: 8
  merge_gap_ms: 250

subtitle:
  font_name: Arial
  font_size: 22
  margin_v: 18
  alignment: 2
  primary_color: "&H00FFFFFF"
  outline_color: "&H00000000"
  outline: 2
  shadow: 1

compose:
  audio_mode: dub_only
  enable_source_separation: false
  source_separation_provider: demucs
  bgm_gain_db: -12
  video_codec: libx264
  crf: 23
  preset: medium
  audio_codec: aac
  audio_bitrate: 192k
```

### 环境变量

敏感信息不写入 `config.yaml`，统一放到环境变量：

- `ANTHROPIC_API_KEY`
- 未来如接入远程 TTS / ASR，再新增对应 Key

提供 `.env.example` 作为模板。

---

## Phase 0：技术可行性验证（先做）

目标：在正式编码前，先验证 Apple Silicon 上最关键的 3 个环节。

### Spike 0.1：FFmpeg 音频抽取与字幕烧录

输入：1 条 30-90 秒样本视频

验证内容：
- 能否稳定抽取 `wav`
- 能否用 ASS 叠加英文字幕
- 输出视频是否同步、可播放

产出：
- 一条手工构造字幕和音频的样例输出视频

通过标准：
- `ffmpeg` 命令稳定可用
- 英文字幕在安全区域内，肉眼可读

### Spike 0.2：ASR 在 Apple Silicon 跑通

验证内容：
- `Qwen3-ASR` 是否可运行
- 单条视频转写耗时是否可接受
- 如果失败，`faster-whisper` 是否可替代

通过标准：
- 至少有 1 个 ASR 方案在本机稳定跑通
- 生成 SRT 的时间轴和文本质量可接受

### Spike 0.3：TTS 在 Apple Silicon 跑通

验证内容：
- `VoxCPM2` 是否能生成稳定英文配音
- 速度、显存/内存占用是否可接受
- 如果失败，`Kokoro` 或远程 TTS 是否可替代

通过标准：
- 至少有 1 个 TTS 方案在本机稳定跑通
- 生成语音自然度和速度达到 MVP 要求

### Phase 0 决策门

只有当以下条件满足后，才进入正式实现：

1. FFmpeg 流程稳定
2. 至少 1 个 ASR 方案可用
3. 至少 1 个 TTS 方案可用

如果 `Qwen3-ASR` 或 `VoxCPM2` 任一不可用，不阻塞项目，直接切换到备选实现。

---

## Phase 1：工程骨架与可恢复执行

### Step 1：项目初始化

文件：`pyproject.toml`, `config.yaml`, `.env.example`

依赖建议：

```text
anthropic
pysubs2
pyyaml
python-dotenv
pydantic
```

按最终选型追加：

```text
qwen3-asr-toolkit 或 faster-whisper
voxcpm 或 kokoro
```

系统依赖：

```bash
brew install ffmpeg
```

验收标准：
- 能正确读取配置与环境变量
- 能创建 `input/ output/ workspace/`
- `python -m scripts.process_single --help` 可运行

### Step 2：统一数据结构和状态管理

文件：`src/models.py`, `src/utils.py`

定义至少两类对象：

```python
class SubtitleSegment:
    index: int
    start_ms: int
    end_ms: int
    text: str

class JobState:
    asr: str
    translate: str
    tts: str
    subtitle: str
    compose: str
```

要求：
- 所有模块都基于统一字幕结构工作
- 每步完成后写入 `status.json`
- 已完成步骤在 `resume=true` 时自动跳过

验收标准：
- 同一个视频重复执行时，不会重复跑已完成步骤

---

## Phase 2：核心处理链路

### Step 3：ASR 模块 `asr.py`

功能：输入视频文件，输出 `source_audio.wav` 和 `zh.srt`

接口建议：

```python
def transcribe(video_path: Path, audio_path: Path, output_srt: Path, config: dict) -> Path:
    """抽取音频并转写为中文 SRT。"""
```

实现要点：
- 使用 FFmpeg 从视频抽取单声道或双声道 WAV
- 优先调用主 ASR，失败后自动切到 fallback
- 输出标准 SRT，而不是自由文本
- 记录选中的 ASR 提供方与耗时到 `manifest.json`

验收标准：
- 1 条视频成功产出合法 `zh.srt`
- 时间轴可播放检查，文本质量可接受

### Step 4：翻译模块 `translate.py`

功能：输入 `zh.srt`，输出 `en.srt`

接口建议：

```python
def translate_srt(zh_srt: Path, en_srt: Path, config: dict) -> Path:
    """只翻译文本字段，保留原时间轴和段号。"""
```

实现要点：
- 先解析 SRT 为 `SubtitleSegment[]`
- 将文本按批次发送给 Claude API
- Prompt 只要求翻译文本，不返回时间轴
- 程序使用原 `index/start/end` 重建英文 SRT
- 翻译后做长度检查，必要时二次压缩文案

建议约束：
- 口语化、自然，不直译
- 保留语气与信息密度
- 控制语速，目标不超过约 `140 wpm`

验收标准：
- 英文 SRT 段数与中文一致
- 时间轴完全保留
- 不出现非法 SRT 格式

### Step 5：TTS 模块 `tts.py`

功能：输入 `en.srt`，输出 `voiceover.wav`

接口建议：

```python
def generate_voiceover(en_srt: Path, output_wav: Path, config: dict) -> Path:
    """生成英文配音并做时长对齐。"""
```

实现要点：
- 先解析 `en.srt`
- 对过短片段做合并，降低逐句断裂感
- 调用 TTS 逐段生成音频片段
- 对每段做对齐：
  - 短于目标时长：补静音
  - 长于目标时长：优先轻微调速，超过阈值再截断或回退缩短文本
- 拼接为完整 `voiceover.wav`
- 输出前做响度归一化，保证批量视频音量一致性

建议策略：
- `min_segment_chars < 8` 时尝试与前后段合并
- 最大调速不超过 `1.30x`
- 生成失败时记录失败片段，便于定位

验收标准：
- 能稳定生成完整 WAV
- 与目标时间轴基本对齐
- 不出现明显爆音、断句异常、长时间静音错位

### Step 6：字幕模块 `subtitle.py`

功能：输入 `en.srt`，输出 `en.ass`

接口建议：

```python
def srt_to_styled_ass(srt_path: Path, ass_path: Path, style_config: dict) -> Path:
    """将英文 SRT 转为带样式的 ASS 字幕。"""
```

实现要点：
- 使用 `pysubs2` 转换格式
- 样式集中放在 `config.yaml`
- 默认放在底部居中，但应预留可调 `margin_v`
- 后续可按视频系列定义多套样式模板

验收标准：
- 英文字幕清晰可读
- 不与烧录中文字幕严重重叠

### Step 7：合成模块 `compose.py`

功能：原视频 + 英文配音 + 英文字幕 → 最终视频

接口建议：

```python
def compose_video(video_path: Path, voiceover_path: Path, ass_path: Path, output_path: Path, config: dict) -> Path:
    """叠加字幕并输出英文版视频。"""
```

默认不要只做“纯替换原音轨”，建议支持两种模式：

1. `dub_only`：完全替换原音轨
2. `dub_plus_bgm`：保留原音轨低音量作为背景氛围

MVP 推荐默认模式：`dub_only`

原因：
- 原音轨里通常同时包含 BGM、环境音和中文人声
- 如果只是整体压低原音轨，中文人声仍会和英文配音重叠
- MVP 先保证英文配音清晰、实现简单、结果可控

`dub_plus_bgm` 作为 Phase 2 优化项引入，前提是先做人声分离：

- 可选方案：`Demucs` 等 vocal remover
- 目标是从原音轨中分离出 accompaniment / bgm stem
- 只有在“中文人声被有效去除”时，才把背景音与英文配音混合
- 如果分离效果不稳定，自动回退到 `dub_only`

FFmpeg 方向：
- 视频流沿用原视频
- 字幕通过 `ass=` 叠加
- MVP：直接使用英文配音作为输出音轨
- Phase 2：将分离后的 BGM/环境音降音量后与英文配音混音

验收标准：
- 输出视频稳定可播放
- 画面、字幕、配音同步
- MVP 下英文配音清晰，无中文人声串扰
- 如果后续开启背景音，英文配音仍清晰可辨

---

## Phase 3：流程编排与批处理

### Step 8：主流程编排 `pipeline.py`

功能：串联 ASR、翻译、TTS、字幕、合成

接口建议：

```python
def process_video(video_path: Path, config: dict) -> Path:
    """处理单个视频，支持断点续跑。"""
```

要求：
- 每步前检查 `status.json`
- 每步后记录耗时和输出文件
- 任一步失败时抛出清晰错误信息
- 不删除已成功的中间产物

验收标准：
- 同一视频可重复执行
- 失败后修复问题再重跑，可从断点继续

### Step 9：批量脚本 `process_batch.py`

功能：扫描 `input/` 目录，逐条处理视频

要求：
- 默认串行执行
- 打印当前进度、成功数、失败数
- 为失败视频输出错误摘要
- 最终输出一份批处理结果汇总

不建议 MVP 阶段做并发：
- Apple Silicon 上 TTS 占用高
- 模型加载和音频处理更适合串行稳定执行

验收标准：
- 连续处理 3-5 条视频不崩溃
- 单条失败不影响后续任务继续执行

---

## 建议开发顺序

```text
Phase 0：做 3 个 spike，锁定可用 ASR/TTS 方案
Phase 1：完成配置、状态管理、CLI 骨架
Phase 2：先打通 ASR → 翻译 → 字幕
Phase 2：再接入 TTS 与时长对齐
Phase 2：最后做视频合成与混音
Phase 3：补齐批处理、日志、汇总
```

实际编码顺序建议：

1. `utils.py` / `models.py`
2. `asr.py`
3. `translate.py`
4. `subtitle.py`
5. `tts.py`
6. `compose.py`
7. `pipeline.py`
8. `process_single.py`
9. `process_batch.py`

说明：
- `subtitle.py` 可以先于 `tts.py` 完成，便于先验证“字幕叠加链路”
- `tts.py` 是最不稳定的一环，放在基础结构稳定后接入更稳妥

---

## 验证计划

### 1. 单环节验证

- ASR：中文文本是否基本正确，时间轴是否可用
- 翻译：段数是否一致，英文是否自然，长度是否适中
- TTS：语音是否自然，时长是否接近目标区间
- 字幕：位置是否安全，样式是否清晰
- 合成：输出文件是否正常播放、音画是否同步

### 2. 端到端验证

准备 1 条 1-2 分钟样本视频，要求：
- 完整跑通所有步骤
- 生成最终视频
- 肉眼验证可发布性

### 3. 批量稳定性验证

准备 3-5 条视频：
- 至少包含不同语速
- 至少包含不同字幕烧录位置
- 至少包含一条带明显 BGM 的视频

观察：
- 是否有步骤经常失败
- 是否存在字幕遮挡
- 是否存在音量不一致

### 4. 性能验证

记录每一步耗时：
- ASR 耗时
- 翻译耗时
- TTS 耗时
- 合成耗时

目标是评估：
- 单条视频平均耗时
- 每周处理 30 条是否现实

---

## 风险与备选方案

| 风险 | 影响 | 备选 |
|------|------|------|
| `VoxCPM2` 在 Apple Silicon 太慢或不稳定 | TTS 成为瓶颈 | 切 `Kokoro`，或接远程 TTS API |
| `Qwen3-ASR` 不支持或效果不稳定 | ASR 无法落地 | 切 `faster-whisper` |
| 直接整份 SRT 交给 LLM 破坏格式 | 翻译步骤不稳定 | 程序先解析，再只翻文本 |
| 逐句 TTS 断裂感强 | 成片不自然 | 合并短段、轻度调速、必要时改文案 |
| 原音轨直接降音量后仍保留中文人声 | 中英人声重叠 | MVP 用 `dub_only`，后续接 `Demucs` 做人声分离 |
| 原音轨被完全替换导致失去 BGM/氛围 | 成片质感下降 | 作为 Phase 2 引入 `dub_plus_bgm` |
| 不同视频中文字幕位置差异大 | 英文字幕遮挡 | 采用多套字幕样式模板，按系列选择 |
| 批处理中途失败导致重复跑耗时步骤 | 效率低 | `status.json` + `resume=true` |

---

## 最终交付标准

满足以下条件即可认为 MVP 完成：

1. 能稳定处理单条视频并输出英文版成片
2. 能批量串行处理至少 3-5 条视频
3. 支持失败后从中间步骤恢复
4. 字幕、配音、画面三者基本同步
5. 在 Apple Silicon Mac 上可重复运行
6. 能根据实际情况切换 ASR/TTS 备选实现

如果 Phase 0 发现本地模型不可行，则项目目标不变，但技术路线调整为：

- 本地做：FFmpeg、字幕、编排、批处理
- 云端做：翻译和/或 TTS

核心原则是：优先完成自动化流水线，而不是坚持某一个模型必须落地。
