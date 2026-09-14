from collections.abc import Sequence
from pathlib import Path

from video_extract_skill.domain import Cue, LocalizationConfig
from video_extract_skill.pipeline import localize_audio


class FakeProvider:
    name = "fake-cloud"

    def __init__(self) -> None:
        self.translations = 0
        self.syntheses = 0

    def translate(self, text: str, *, source: str, target: str):
        self.translations += 1
        return "你好。", len(text)

    def synthesize(self, text: str, *, voice: int, sample_rate: int, speed: float):
        self.syntheses += 1
        return b"a" * 101

    def transcribe(self, audio: Path, *, offset: float, diarize: bool):
        return [Cue("cloud-1", offset, offset + 2, "hello")]


class FakeMedia:
    def duration(self, media: Path) -> float:
        if media.name == "podcast.zh-CN.m4a":
            return 4.0
        if media.suffix == ".mp3":
            return 1.0
        return 4.0

    def split_for_asr(self, audio: Path, output: Path, *, seconds: int, bitrate: str):
        output.mkdir(parents=True, exist_ok=True)
        chunk = output / "chunk.mp3"
        chunk.write_bytes(b"input")
        return [chunk]

    def render_segment(self, audio: Path, output: Path, **kwargs) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered")

    def concatenate(
        self, inputs: Sequence[Path], output: Path, *, duration: float
    ) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")


def test_existing_subtitle_skips_asr_and_cache_resumes(tmp_path: Path) -> None:
    audio = tmp_path / "source.m4a"
    subtitle = tmp_path / "source.srt"
    audio.write_bytes(b"audio")
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nHello\n\n"
        "2\n00:00:02,500 --> 00:00:04,000\nAgain\n",
        encoding="utf-8",
    )
    provider = FakeProvider()
    media = FakeMedia()
    config = LocalizationConfig(max_tts_chars=70)

    first = localize_audio(
        audio,
        tmp_path / "out",
        subtitle=subtitle,
        config=config,
        provider=provider,
        media=media,
    )
    second = localize_audio(
        audio,
        tmp_path / "out",
        subtitle=subtitle,
        config=config,
        provider=provider,
        media=media,
    )

    assert first.output.is_file() and first.report.is_file()
    assert provider.translations == 1
    assert provider.syntheses == 1
    assert second.cache_hits == 2


def test_missing_subtitle_uses_cloud_asr(tmp_path: Path) -> None:
    audio = tmp_path / "source.m4a"
    audio.write_bytes(b"audio")

    result = localize_audio(
        audio, tmp_path / "out", provider=FakeProvider(), media=FakeMedia()
    )

    assert result.segment_count == 1
