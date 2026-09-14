"""Typed domain objects shared by the localization pipeline and adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Cue:
    id: str
    start: float
    end: float
    text: str
    speaker: str = "unknown"

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError(f"invalid cue interval: {self.start}..{self.end}")
        if not self.text.strip():
            raise ValueError("cue text must not be empty")


@dataclass(frozen=True)
class LocalizedSegment:
    id: str
    start: float
    end: float
    text: str
    source_ids: tuple[str, ...]
    speaker: str
    voice: int


@dataclass(frozen=True)
class TranslationUnit:
    id: str
    start: float
    end: float
    text: str
    source_ids: tuple[str, ...]
    speaker: str


@dataclass(frozen=True)
class SynthesizedSegment:
    segment: LocalizedSegment
    audio: Path
    duration: float
    cache_hit: bool


@dataclass(frozen=True)
class LocalizationConfig:
    region: str = "ap-guangzhou"
    source_language: str = "en"
    target_language: str = "zh"
    default_voice: int = 501005
    speaker_voices: dict[str, int] = field(
        default_factory=lambda: {"speaker_0": 501005, "speaker_1": 101001}
    )
    sample_rate: int = 24000
    tts_speed: float = 0.0
    translation_concurrency: int = 5
    tts_concurrency: int = 3
    max_translation_chars: int = 1800
    min_tts_chars: int = 40
    max_tts_chars: int = 70
    max_speed_ratio: float = 1.35
    max_attempts: int = 3
    asr_chunk_seconds: int = 600
    asr_bitrate: str = "32k"
    diarize: bool = True

    def __post_init__(self) -> None:
        if self.translation_concurrency < 1 or self.tts_concurrency < 1:
            raise ValueError("cloud concurrency must be positive")
        if not 1.0 <= self.max_speed_ratio <= 2.0:
            raise ValueError("max_speed_ratio must be between 1.0 and 2.0")
        if self.max_tts_chars < 1 or self.max_translation_chars < 1:
            raise ValueError("text limits must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")


@dataclass(frozen=True)
class LocalizationResult:
    output: Path
    report: Path
    source_duration: float
    segment_count: int
    translated_characters: int
    tts_characters: int
    cache_hits: int
    elapsed_seconds: float
