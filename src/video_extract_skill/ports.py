"""External boundaries used by the application pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from .domain import Cue


class CloudProvider(Protocol):
    name: str

    def translate(self, text: str, *, source: str, target: str) -> tuple[str, int]: ...

    def synthesize(
        self, text: str, *, voice: int, sample_rate: int, speed: float
    ) -> bytes: ...

    def transcribe(
        self, audio: Path, *, offset: float, diarize: bool
    ) -> Sequence[Cue]: ...


class MediaRunner(Protocol):
    def duration(self, media: Path) -> float: ...

    def split_for_asr(
        self, audio: Path, output: Path, *, seconds: int, bitrate: str
    ) -> list[Path]: ...

    def render_segment(
        self,
        audio: Path,
        output: Path,
        *,
        leading_silence: float,
        slot_duration: float,
        speed_ratio: float,
    ) -> None: ...

    def concatenate(
        self, inputs: Sequence[Path], output: Path, *, duration: float
    ) -> None: ...
