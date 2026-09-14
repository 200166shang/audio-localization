"""Application orchestration for resumable Tencent Cloud localization."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .cache import Cache, key
from .domain import (
    Cue,
    LocalizationConfig,
    LocalizationResult,
    LocalizedSegment,
    SynthesizedSegment,
    TranslationUnit,
)
from .errors import ConfigurationError, TimelineError
from .media import FFmpegMediaRunner
from .ports import CloudProvider, MediaRunner
from .srt import parse_srt
from .tencent import TencentProvider


def localize_audio(
    audio: Path,
    output: Path,
    *,
    subtitle: Path | None = None,
    config: LocalizationConfig | None = None,
    provider: CloudProvider | None = None,
    media: MediaRunner | None = None,
) -> LocalizationResult:
    """Create a timeline-aligned Mandarin track and a resumable run report."""
    started = time.monotonic()
    config = config or LocalizationConfig()
    audio, output = audio.expanduser().resolve(), output.expanduser().resolve()
    if not audio.is_file():
        raise FileNotFoundError(audio)
    output.mkdir(parents=True, exist_ok=True)
    provider = provider or TencentProvider(config)
    media = media or FFmpegMediaRunner()
    source_duration = media.duration(audio)
    cache = Cache(output / ".work" / "tencent")

    phase_started = time.monotonic()
    cues = (
        parse_srt(subtitle.expanduser().resolve())
        if subtitle
        else _transcribe(audio, output, provider, media, config)
    )
    timings = {"transcription_seconds": time.monotonic() - phase_started}

    units = _translation_units(cues, min(config.max_translation_chars, 600))
    phase_started = time.monotonic()
    translated, translation_chars, translation_hits = _translate(
        units, cache, provider, config
    )
    timings["translation_seconds"] = time.monotonic() - phase_started

    segments = _localized_segments(translated, config)
    _assert_source_coverage(cues, segments)
    script_path = output / "localization" / "script.zh-CN.json"
    _write_json(
        script_path,
        {
            "schema": "tencent-localized-script-v1",
            "segments": [_segment_json(segment) for segment in segments],
        },
    )

    phase_started = time.monotonic()
    synthesized, tts_chars, tts_hits = _synthesize(
        segments, cache, provider, media, config
    )
    timings["tts_seconds"] = time.monotonic() - phase_started

    phase_started = time.monotonic()
    rendered = _render_timeline(synthesized, output, media, source_duration, config)
    final_audio = output / "audio" / "podcast.zh-CN.m4a"
    media.concatenate(rendered, final_audio, duration=source_duration)
    final_duration = media.duration(final_audio)
    if abs(final_duration - source_duration) > 0.1:
        raise TimelineError(
            f"final duration differs from source by {abs(final_duration - source_duration):.3f}s"
        )
    timings["timeline_seconds"] = time.monotonic() - phase_started
    timings["total_seconds"] = time.monotonic() - started

    report_path = output / "localization" / "report.json"
    _write_json(
        report_path,
        {
            "schema": "tencent-localization-report-v1",
            "provider": provider.name,
            "source_audio": str(audio),
            "source_subtitle": str(subtitle.expanduser().resolve())
            if subtitle
            else None,
            "output_audio": str(final_audio),
            "source_duration": source_duration,
            "output_duration": final_duration,
            "source_cues": len(cues),
            "segments": len(segments),
            "translated_characters": translation_chars,
            "tts_characters": tts_chars,
            "cache_hits": translation_hits + tts_hits,
            "timings": timings,
            "voice": config.default_voice,
            "speaker_voices": config.speaker_voices,
        },
    )
    return LocalizationResult(
        final_audio,
        report_path,
        source_duration,
        len(segments),
        translation_chars,
        tts_chars,
        translation_hits + tts_hits,
        timings["total_seconds"],
    )


def _transcribe(
    audio: Path,
    output: Path,
    provider: CloudProvider,
    media: MediaRunner,
    config: LocalizationConfig,
) -> list[Cue]:
    chunks = media.split_for_asr(
        audio,
        output / ".work" / "asr-input",
        seconds=config.asr_chunk_seconds,
        bitrate=config.asr_bitrate,
    )
    cues: list[Cue] = []
    offset = 0.0
    for chunk in chunks:
        cues.extend(provider.transcribe(chunk, offset=offset, diarize=config.diarize))
        offset += media.duration(chunk)
    return cues


def _translation_units(cues: Sequence[Cue], max_chars: int) -> list[TranslationUnit]:
    units: list[TranslationUnit] = []
    current: list[Cue] = []
    for cue in cues:
        if len(cue.text) > max_chars:
            if current:
                units.append(_unit(current))
                current = []
            pieces = _split_text(cue.text, max_chars)
            span = cue.end - cue.start
            total = sum(len(piece) for piece in pieces)
            cursor = cue.start
            for index, piece in enumerate(pieces):
                end = (
                    cue.end
                    if index == len(pieces) - 1
                    else cursor + span * len(piece) / total
                )
                units.append(
                    TranslationUnit(
                        f"u-{cue.id}-{index}",
                        cursor,
                        end,
                        piece,
                        (cue.id,),
                        cue.speaker,
                    )
                )
                cursor = end
            continue
        next_size = (
            sum(len(item.text) for item in current) + len(cue.text) + len(current)
        )
        incompatible = current and (
            cue.speaker != current[-1].speaker
            or cue.start - current[-1].end > 1.25
            or cue.start < current[-1].end
            or next_size > max_chars
        )
        if incompatible:
            units.append(_unit(current))
            current = []
        current.append(cue)
    if current:
        units.append(_unit(current))
    return units


def _unit(cues: Sequence[Cue]) -> TranslationUnit:
    return TranslationUnit(
        f"u-{cues[0].id}",
        cues[0].start,
        cues[-1].end,
        " ".join(cue.text for cue in cues),
        tuple(cue.id for cue in cues),
        cues[0].speaker,
    )


def _translate(
    units: Sequence[TranslationUnit],
    cache: Cache,
    provider: CloudProvider,
    config: LocalizationConfig,
) -> tuple[list[tuple[TranslationUnit, str]], int, int]:
    def one(unit: TranslationUnit) -> tuple[TranslationUnit, str, int, bool]:
        cache_key = key(
            "translation",
            {
                "provider": provider.name,
                "text": unit.text,
                "source": config.source_language,
                "target": config.target_language,
            },
        )
        cached = cache.read_json("translation", cache_key)
        if cached and str(cached.get("text") or "").strip():
            return unit, str(cached["text"]), int(cached.get("used_amount") or 0), True
        text, used = provider.translate(
            unit.text, source=config.source_language, target=config.target_language
        )
        cache.write_json("translation", cache_key, {"text": text, "used_amount": used})
        return unit, text, used, False

    with ThreadPoolExecutor(max_workers=config.translation_concurrency) as executor:
        values = list(executor.map(one, units))
    return (
        [(unit, text) for unit, text, _, _ in values],
        sum(used for _, _, used, _ in values),
        sum(hit for *_, hit in values),
    )


def _localized_segments(
    translated: Sequence[tuple[TranslationUnit, str]], config: LocalizationConfig
) -> list[LocalizedSegment]:
    segments: list[LocalizedSegment] = []
    for unit, text in translated:
        pieces = _split_text(text, config.max_tts_chars)
        total = sum(len(piece) for piece in pieces)
        cursor = unit.start
        for index, piece in enumerate(pieces):
            end = (
                unit.end
                if index == len(pieces) - 1
                else cursor + (unit.end - unit.start) * len(piece) / total
            )
            segments.append(
                LocalizedSegment(
                    f"{unit.id}-{index}",
                    cursor,
                    end,
                    piece,
                    unit.source_ids,
                    unit.speaker,
                    config.speaker_voices.get(unit.speaker, config.default_voice),
                )
            )
            cursor = end
    return segments


def _split_text(text: str, limit: int) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[。！？；，,.!?;])", text)
        if part.strip()
    ]
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        if current and len(current) + len(sentence) > limit:
            pieces.append(current)
            current = ""
        current += sentence
    if current:
        pieces.append(current)
    return pieces


def _synthesize(
    segments: Sequence[LocalizedSegment],
    cache: Cache,
    provider: CloudProvider,
    media: MediaRunner,
    config: LocalizationConfig,
) -> tuple[list[SynthesizedSegment], int, int]:
    def one(segment: LocalizedSegment) -> SynthesizedSegment:
        cache_key = key(
            "tts",
            {
                "provider": provider.name,
                "text": segment.text,
                "voice": segment.voice,
                "sample_rate": config.sample_rate,
                "speed": config.tts_speed,
                "codec": "mp3",
            },
        )
        audio_path = cache.audio_path(cache_key)
        if audio_path.is_file():
            try:
                return SynthesizedSegment(
                    segment, audio_path, media.duration(audio_path), True
                )
            except (ConfigurationError, OSError):
                audio_path.unlink(missing_ok=True)
        data = provider.synthesize(
            segment.text,
            voice=segment.voice,
            sample_rate=config.sample_rate,
            speed=config.tts_speed,
        )
        audio_path = cache.write_audio(cache_key, data)
        return SynthesizedSegment(
            segment, audio_path, media.duration(audio_path), False
        )

    with ThreadPoolExecutor(max_workers=config.tts_concurrency) as executor:
        values = list(executor.map(one, segments))
    return (
        values,
        sum(len(item.segment.text) for item in values),
        sum(item.cache_hit for item in values),
    )


def _render_timeline(
    synthesized: Sequence[SynthesizedSegment],
    output: Path,
    media: MediaRunner,
    source_duration: float,
    config: LocalizationConfig,
) -> list[Path]:
    cursor = 0.0
    rendered: list[Path] = []
    for index, item in enumerate(synthesized):
        segment = item.segment
        leading = max(0.0, segment.start - cursor)
        slot = segment.end - segment.start
        ratio = max(1.0, item.duration / slot)
        if ratio > config.max_speed_ratio:
            raise TimelineError(
                f"segment {segment.id} needs {ratio:.2f}x speed; maximum is {config.max_speed_ratio:.2f}x"
            )
        target = output / ".work" / "timeline" / f"{index:05d}.wav"
        media.render_segment(
            item.audio,
            target,
            leading_silence=leading,
            slot_duration=slot,
            speed_ratio=ratio,
        )
        rendered.append(target)
        cursor = segment.end
    if cursor > source_duration + 0.1:
        raise TimelineError("localized timeline exceeds source duration")
    return rendered


def _assert_source_coverage(
    cues: Sequence[Cue], segments: Sequence[LocalizedSegment]
) -> None:
    expected = {cue.id for cue in cues}
    actual = {source_id for segment in segments for source_id in segment.source_ids}
    if expected != actual:
        raise ValueError(
            f"localized script coverage mismatch: missing={expected - actual}, extra={actual - expected}"
        )


def _segment_json(segment: LocalizedSegment) -> dict[str, object]:
    return {
        "id": segment.id,
        "start": segment.start,
        "end": segment.end,
        "text": segment.text,
        "source_ids": list(segment.source_ids),
        "speaker": segment.speaker,
        "voice": segment.voice,
    }


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.partial")
    try:
        partial.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
