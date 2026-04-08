from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

StepStatus = Literal["pending", "running", "completed", "failed", "skipped"]


@dataclass(slots=True)
class SubtitleSegment:
    index: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(slots=True)
class MergedSegment:
    indices: list[int]
    start_ms: int
    end_ms: int
    text: str


@dataclass
class JobState:
    asr: StepStatus = "pending"
    translate: StepStatus = "pending"
    tts: StepStatus = "pending"
    subtitle: StepStatus = "pending"
    compose: StepStatus = "pending"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JobState":
        if not data:
            return cls()
        return cls(
            asr=data.get("asr", "pending"),
            translate=data.get("translate", "pending"),
            tts=data.get("tts", "pending"),
            subtitle=data.get("subtitle", "pending"),
            compose=data.get("compose", "pending"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def get(self, step_name: str) -> StepStatus:
        return getattr(self, step_name)

    def set(self, step_name: str, status: StepStatus) -> None:
        setattr(self, step_name, status)


@dataclass
class StepRecord:
    provider: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    inputs: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "StepRecord":
        if not data:
            return cls()
        return cls(
            provider=data.get("provider"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            duration_seconds=data.get("duration_seconds"),
            inputs=dict(data.get("inputs", {})),
            outputs=dict(data.get("outputs", {})),
            metadata=dict(data.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JobManifest:
    video_name: str
    source_video: str
    created_at: str
    updated_at: str
    version: str = "0.1.0"
    steps: dict[str, StepRecord] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JobManifest | None":
        if not data:
            return None
        return cls(
            video_name=data["video_name"],
            source_video=data["source_video"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            version=data.get("version", "0.1.0"),
            steps={
                name: StepRecord.from_dict(step_data)
                for name, step_data in dict(data.get("steps", {})).items()
            },
            outputs=dict(data.get("outputs", {})),
            metadata=dict(data.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["steps"] = {name: step.to_dict() for name, step in self.steps.items()}
        return payload


@dataclass(slots=True)
class PathsConfig:
    input_dir: str = "input"
    output_dir: str = "output"
    workspace_dir: str = "workspace"


@dataclass(slots=True)
class RuntimeConfig:
    resume: bool = True
    overwrite: bool = False
    log_level: str = "INFO"
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"


@dataclass(slots=True)
class AsrConfig:
    provider: str = "qwen3_asr"
    fallback_provider: str | None = "faster_whisper"
    language: str = "zh"
    sample_rate: int = 16000
    channels: int = 1
    qwen3_command: str | None = None
    qwen3_model_id: str = "Qwen/Qwen3-ASR-0.6B"
    qwen3_forced_aligner_id: str = "Qwen/Qwen3-ForcedAligner-0.6B"
    qwen3_device: str = "auto"
    qwen3_dtype: str = "float32"
    qwen3_attn_implementation: str = "sdpa"
    qwen3_max_new_tokens: int = 4096
    qwen3_max_segment_chars: int = 24
    qwen3_max_segment_ms: int = 4000
    faster_whisper_model: str = "large-v3"
    device: str = "cpu"
    compute_type: str = "int8"


@dataclass(slots=True)
class TranslateConfig:
    provider: str = "claude_code"
    model: str = "sonnet"
    batch_size: int = 30
    max_words_per_minute: int = 140
    contextual_smoothing: bool = True
    temperature: float = 0.1
    claude_code_bin: str = "claude"
    claude_code_permission_mode: str = "bypassPermissions"
    claude_code_tools: str = ""


@dataclass(slots=True)
class TtsConfig:
    provider: str = "vibevoice_realtime"
    fallback_provider: str | None = "macos_say"
    voice_mode: str = "description"
    voice_description: str = (
        "A young adult, natural, energetic, clear American English voice"
    )
    reference_wav: str | None = None
    max_tempo: float = 1.30
    min_segment_chars: int = 8
    merge_gap_ms: int = 250
    sentence_aware_merge: bool = True
    sentence_merge_max_duration_ms: int = 17000
    sentence_merge_max_chars: int = 220
    smooth_merged_text: bool = True
    sample_rate: int = 24000
    normalize_lufs: int = -16
    vibevoice_realtime_command: str | None = None
    vibevoice_model_path: str = "microsoft/VibeVoice-Realtime-0.5B"
    vibevoice_repo_dir: str | None = None
    vibevoice_voice_prompt_pt: str | None = None
    vibevoice_speaker_name: str = "wayne"
    vibevoice_device: str = "auto"
    vibevoice_cfg_scale: float = 1.5
    voxcpm2_command: str | None = None
    kokoro_command: str | None = None
    macos_voice: str = "Samantha"


@dataclass(slots=True)
class SubtitleStyleConfig:
    font_name: str = "Arial"
    font_size: int = 14
    margin_v: int = 18
    alignment: int = 2
    primary_color: str = "&H00FFFFFF"
    outline_color: str = "&H00000000"
    outline: int = 2
    shadow: int = 1


@dataclass(slots=True)
class ComposeConfig:
    audio_mode: str = "dub_plus_bgm"
    enable_source_separation: bool = True
    source_separation_provider: str = "demucs"
    source_separation_model: str = "htdemucs"
    source_separation_device: str = "cpu"
    bgm_gain_db: float = -12
    video_codec: str = "libx264"
    crf: int = 23
    preset: str = "medium"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"


@dataclass(slots=True)
class AppConfig:
    paths: PathsConfig = field(default_factory=PathsConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    translate: TranslateConfig = field(default_factory=TranslateConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    subtitle: SubtitleStyleConfig = field(default_factory=SubtitleStyleConfig)
    compose: ComposeConfig = field(default_factory=ComposeConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppConfig":
        data = data or {}
        return cls(
            paths=PathsConfig(**dict(data.get("paths", {}))),
            runtime=RuntimeConfig(**dict(data.get("runtime", {}))),
            asr=AsrConfig(**dict(data.get("asr", {}))),
            translate=TranslateConfig(**dict(data.get("translate", {}))),
            tts=TtsConfig(**dict(data.get("tts", {}))),
            subtitle=SubtitleStyleConfig(**dict(data.get("subtitle", {}))),
            compose=ComposeConfig(**dict(data.get("compose", {}))),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
